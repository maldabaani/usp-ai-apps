package com.jslogicextractor.batch;

import com.anthropic.client.AnthropicClient;
import com.anthropic.core.http.StreamResponse;
import com.anthropic.models.ErrorResponse;
import com.anthropic.models.messages.Message;
import com.anthropic.models.messages.TextBlock;
import com.anthropic.models.messages.Usage;
import com.anthropic.models.messages.batches.BatchCreateParams;
import com.anthropic.models.messages.batches.MessageBatch;
import com.anthropic.models.messages.batches.MessageBatchIndividualResponse;
import com.anthropic.models.messages.batches.MessageBatchRequestCounts;
import com.anthropic.services.blocking.MessageService;
import com.anthropic.services.blocking.messages.BatchService;
import com.jslogicextractor.agent.ExtractionResult;
import com.jslogicextractor.config.BatchExtractionProperties;
import com.jslogicextractor.orchestration.ExtractionJob;
import com.jslogicextractor.output.ExtractionResultWriter;
import com.jslogicextractor.prompt.LogicExtractionPromptTemplates;
import com.jslogicextractor.scanner.SourceFile;
import org.junit.jupiter.api.Test;

import java.nio.file.Path;
import java.time.Duration;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.stream.Stream;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class BatchExtractionServiceTest {

    private final AnthropicClient anthropicClient = mock(AnthropicClient.class);
    private final MessageService messageService = mock(MessageService.class);
    private final BatchService batchService = mock(BatchService.class);
    private final List<ExtractionResult> writtenResults = new CopyOnWriteArrayList<>();

    private final BatchExtractionProperties batchProperties = new BatchExtractionProperties(
            "claude-sonnet-4-5-20250929", 4096, 0.0, Duration.ofMillis(10), Duration.ofMinutes(1),
            10_000, 200_000_000L);

    private final LogicExtractionPromptTemplates promptTemplates = new LogicExtractionPromptTemplates();

    private final ExtractionResultWriter resultWriter = new ExtractionResultWriter() {
        @Override
        public boolean exists(ExtractionJob job, String relativePath) {
            return false;
        }

        @Override
        public void write(ExtractionJob job, ExtractionResult result) {
            writtenResults.add(result);
        }

        @Override
        public void writeSummary(ExtractionJob job) {
        }
    };

    private final BatchExtractionService service =
            new BatchExtractionService(anthropicClient, batchProperties, promptTemplates, resultWriter);

    @Test
    void mapsSucceededAndErroredResultsAndRecordsCounts() {
        when(anthropicClient.messages()).thenReturn(messageService);
        when(messageService.batches()).thenReturn(batchService);

        SourceFile succeededFile = file("a.js", "const a = 1;");
        SourceFile erroredFile = file("b.js", "const b = 2;");

        MessageBatch endedBatch = batch("batch_123", MessageBatch.ProcessingStatus.ENDED);
        when(batchService.create(any(BatchCreateParams.class))).thenReturn(endedBatch);

        Message succeededMessage = Message.builder()
                .id("msg_1")
                .container(Optional.empty())
                .addContent(TextBlock.builder()
                        .citations(Optional.empty())
                        .text("{\"summary\":\"adds nothing interesting\"}")
                        .build())
                .model("claude-sonnet-4-5-20250929")
                .stopDetails(Optional.empty())
                .stopReason(Optional.empty())
                .stopSequence(Optional.empty())
                .usage(Usage.builder()
                        .cacheCreation(Optional.empty())
                        .cacheCreationInputTokens(0L)
                        .cacheReadInputTokens(0L)
                        .inferenceGeo(Optional.empty())
                        .inputTokens(100L)
                        .outputTokens(50L)
                        .outputTokensDetails(Optional.empty())
                        .serverToolUse(Optional.empty())
                        .serviceTier(Optional.empty())
                        .build())
                .build();

        ErrorResponse errorResponse = ErrorResponse.builder()
                .invalidRequestErrorError("file too large")
                .requestId(Optional.empty())
                .build();

        List<MessageBatchIndividualResponse> individualResponses = List.of(
                MessageBatchIndividualResponse.builder().customId("f0").succeededResult(succeededMessage).build(),
                MessageBatchIndividualResponse.builder().customId("f1").erroredResult(errorResponse).build());

        when(batchService.resultsStreaming("batch_123")).thenReturn(streamOf(individualResponses));

        ExtractionJob job = new ExtractionJob(UUID.randomUUID(), Path.of("/repo"), Path.of("/out"), 4);

        service.runBatch(job, List.of(succeededFile, erroredFile));

        assertThat(job.succeededCount()).isEqualTo(1);
        assertThat(job.failedCount()).isEqualTo(1);
        assertThat(job.processedCount()).isEqualTo(2);

        ExtractionResult succeeded = writtenResults.stream().filter(ExtractionResult::success).findFirst().orElseThrow();
        assertThat(succeeded.relativePath()).isEqualTo("a.js");
        assertThat(succeeded.content()).contains("adds nothing interesting");
        assertThat(succeeded.promptTokens()).isEqualTo(100);
        assertThat(succeeded.completionTokens()).isEqualTo(50);

        ExtractionResult failed = writtenResults.stream().filter(r -> !r.success()).findFirst().orElseThrow();
        assertThat(failed.relativePath()).isEqualTo("b.js");
        assertThat(failed.errorMessage()).contains("file too large");
    }

    @Test
    void failsAllFilesInChunkWhenCreateThrows() {
        when(anthropicClient.messages()).thenReturn(messageService);
        when(messageService.batches()).thenReturn(batchService);
        when(batchService.create(any(BatchCreateParams.class))).thenThrow(new RuntimeException("boom"));

        SourceFile fileA = file("a.js", "const a = 1;");
        SourceFile fileB = file("b.js", "const b = 2;");

        ExtractionJob job = new ExtractionJob(UUID.randomUUID(), Path.of("/repo"), Path.of("/out"), 4);

        service.runBatch(job, List.of(fileA, fileB));

        assertThat(job.failedCount()).isEqualTo(2);
        assertThat(job.succeededCount()).isZero();
        assertThat(writtenResults).hasSize(2);
        assertThat(writtenResults).allSatisfy(result -> {
            assertThat(result.success()).isFalse();
            assertThat(result.errorMessage()).contains("boom");
        });
    }

    @Test
    void splitsFilesAcrossMultipleChunksWhenRequestCapIsExceeded() {
        BatchExtractionProperties tightProperties = new BatchExtractionProperties(
                "claude-sonnet-4-5-20250929", 4096, 0.0, Duration.ofMillis(10), Duration.ofMinutes(1),
                1, 200_000_000L);
        BatchExtractionService chunkedService =
                new BatchExtractionService(anthropicClient, tightProperties, promptTemplates, resultWriter);

        when(anthropicClient.messages()).thenReturn(messageService);
        when(messageService.batches()).thenReturn(batchService);

        SourceFile fileA = file("a.js", "const a = 1;");
        SourceFile fileB = file("b.js", "const b = 2;");

        MessageBatch batch1 = batch("batch_1", MessageBatch.ProcessingStatus.ENDED);
        MessageBatch batch2 = batch("batch_2", MessageBatch.ProcessingStatus.ENDED);
        when(batchService.create(any(BatchCreateParams.class))).thenReturn(batch1, batch2);
        when(batchService.resultsStreaming("batch_1")).thenReturn(streamOf(List.of(
                MessageBatchIndividualResponse.builder().customId("f0").succeededResult(succeededMessage()).build())));
        when(batchService.resultsStreaming("batch_2")).thenReturn(streamOf(List.of(
                MessageBatchIndividualResponse.builder().customId("f0").succeededResult(succeededMessage()).build())));

        ExtractionJob job = new ExtractionJob(UUID.randomUUID(), Path.of("/repo"), Path.of("/out"), 4);

        chunkedService.runBatch(job, List.of(fileA, fileB));

        verify(batchService, times(2)).create(any(BatchCreateParams.class));
        assertThat(job.succeededCount()).isEqualTo(2);
        assertThat(writtenResults).extracting(ExtractionResult::relativePath)
                .containsExactlyInAnyOrder("a.js", "b.js");
    }

    @Test
    void failsFilesWhenBatchNeverReachesEndedBeforeTimeout() {
        BatchExtractionProperties timeoutProperties = new BatchExtractionProperties(
                "claude-sonnet-4-5-20250929", 4096, 0.0, Duration.ofMillis(5), Duration.ofMillis(20),
                10_000, 200_000_000L);
        BatchExtractionService timeoutService =
                new BatchExtractionService(anthropicClient, timeoutProperties, promptTemplates, resultWriter);

        when(anthropicClient.messages()).thenReturn(messageService);
        when(messageService.batches()).thenReturn(batchService);

        MessageBatch inProgressBatch = batch("batch_stuck", MessageBatch.ProcessingStatus.IN_PROGRESS);
        when(batchService.create(any(BatchCreateParams.class))).thenReturn(inProgressBatch);
        when(batchService.retrieve("batch_stuck")).thenReturn(inProgressBatch);

        SourceFile stuckFile = file("a.js", "const a = 1;");
        ExtractionJob job = new ExtractionJob(UUID.randomUUID(), Path.of("/repo"), Path.of("/out"), 4);

        timeoutService.runBatch(job, List.of(stuckFile));

        assertThat(job.failedCount()).isEqualTo(1);
        assertThat(job.succeededCount()).isZero();
        assertThat(writtenResults).hasSize(1);
        ExtractionResult failed = writtenResults.get(0);
        assertThat(failed.success()).isFalse();
        assertThat(failed.errorMessage()).contains("timed out");
    }

    private Message succeededMessage() {
        return Message.builder()
                .id("msg_1")
                .container(Optional.empty())
                .addContent(TextBlock.builder()
                        .citations(Optional.empty())
                        .text("{\"summary\":\"adds nothing interesting\"}")
                        .build())
                .model("claude-sonnet-4-5-20250929")
                .stopDetails(Optional.empty())
                .stopReason(Optional.empty())
                .stopSequence(Optional.empty())
                .usage(Usage.builder()
                        .cacheCreation(Optional.empty())
                        .cacheCreationInputTokens(0L)
                        .cacheReadInputTokens(0L)
                        .inferenceGeo(Optional.empty())
                        .inputTokens(100L)
                        .outputTokens(50L)
                        .outputTokensDetails(Optional.empty())
                        .serverToolUse(Optional.empty())
                        .serviceTier(Optional.empty())
                        .build())
                .build();
    }

    private MessageBatch batch(String id, MessageBatch.ProcessingStatus status) {
        return MessageBatch.builder()
                .id(id)
                .archivedAt(Optional.empty())
                .cancelInitiatedAt(Optional.empty())
                .createdAt(OffsetDateTime.now())
                .endedAt(Optional.empty())
                .expiresAt(OffsetDateTime.now().plusDays(1))
                .processingStatus(status)
                .requestCounts(MessageBatchRequestCounts.builder()
                        .processing(0)
                        .succeeded(1)
                        .errored(1)
                        .canceled(0)
                        .expired(0)
                        .build())
                .resultsUrl(Optional.empty())
                .build();
    }

    private StreamResponse<MessageBatchIndividualResponse> streamOf(List<MessageBatchIndividualResponse> items) {
        return new StreamResponse<>() {
            @Override
            public Stream<MessageBatchIndividualResponse> stream() {
                return items.stream();
            }

            @Override
            public void close() {
            }
        };
    }

    private SourceFile file(String relativePath, String content) {
        return new SourceFile(Path.of(relativePath), relativePath, content, content.length());
    }
}
