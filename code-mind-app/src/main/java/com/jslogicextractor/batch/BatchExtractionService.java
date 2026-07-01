package com.jslogicextractor.batch;

import com.anthropic.client.AnthropicClient;
import com.anthropic.core.http.StreamResponse;
import com.anthropic.models.messages.CacheControlEphemeral;
import com.anthropic.models.messages.ContentBlock;
import com.anthropic.models.messages.Message;
import com.anthropic.models.messages.TextBlockParam;
import com.anthropic.models.messages.Usage;
import com.anthropic.models.messages.batches.BatchCreateParams;
import com.anthropic.models.messages.batches.MessageBatch;
import com.anthropic.models.messages.batches.MessageBatchIndividualResponse;
import com.anthropic.models.messages.batches.MessageBatchResult;
import com.jslogicextractor.agent.ExtractionResult;
import com.jslogicextractor.config.BatchExtractionProperties;
import com.jslogicextractor.orchestration.ExtractionJob;
import com.jslogicextractor.output.ExtractionResultWriter;
import com.jslogicextractor.prompt.LogicExtractionPromptTemplates;
import com.jslogicextractor.scanner.Language;
import com.jslogicextractor.scanner.SourceFile;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.stream.Collectors;

/**
 * Bulk execution path for large repositories: submits every eligible file as one Claude request
 * inside Anthropic Message Batches (flat 50% discount on all token usage vs. the sync path), with
 * the shared extraction instructions cached via a single cache_control breakpoint on the system
 * block so the same prompt text is billed once per cache write instead of once per file.
 *
 * Bypasses Spring AI / {@code LogicExtractionAgent} entirely: spring-ai-anthropic 1.1.7 has no
 * Batches API support, so this talks to the raw Anthropic Java SDK directly. Chunks run
 * sequentially, one batch at a time — sufficient for the 50% cost win this mode exists for; chunk
 * parallelism is a future scaling knob, not implemented here.
 */
@Service
public class BatchExtractionService {

    private static final Logger log = LoggerFactory.getLogger(BatchExtractionService.class);
    private static final String AGENT_NAME = "claude-batch-extractor";
    // Rough JSON structural overhead per request (custom_id, params wrapper, model/maxTokens/temperature fields).
    private static final long REQUEST_OVERHEAD_BYTES = 256;

    private final AnthropicClient anthropicClient;
    private final BatchExtractionProperties batchProperties;
    private final LogicExtractionPromptTemplates promptTemplates;
    private final ExtractionResultWriter resultWriter;

    public BatchExtractionService(AnthropicClient anthropicClient,
                                   BatchExtractionProperties batchProperties,
                                   LogicExtractionPromptTemplates promptTemplates,
                                   ExtractionResultWriter resultWriter) {
        this.anthropicClient = anthropicClient;
        this.batchProperties = batchProperties;
        this.promptTemplates = promptTemplates;
        this.resultWriter = resultWriter;
    }

    public void runBatch(ExtractionJob job, List<SourceFile> files) {
        if (files.isEmpty()) {
            return;
        }

        // Group by language so each group gets its own cached system prompt. Files within a group
        // share a byte-for-byte identical system block, maximising the prompt-cache hit rate.
        Map<Language, List<SourceFile>> byLanguage = files.stream()
                .collect(Collectors.groupingBy(f -> Language.fromPath(f.relativePath())));

        int totalChunks = byLanguage.values().stream()
                .mapToInt(langFiles -> chunkFiles(langFiles, promptTemplates.renderStaticSystemSkeleton(
                        Language.fromPath(langFiles.get(0).relativePath()))).size())
                .sum();
        log.info("Job {}: submitting {} files to Anthropic Batches API across {} batch(es) ({} language group(s))",
                job.id(), files.size(), totalChunks, byLanguage.size());

        for (Map.Entry<Language, List<SourceFile>> entry : byLanguage.entrySet()) {
            Language lang = entry.getKey();
            List<SourceFile> langFiles = entry.getValue();
            String systemSkeleton = promptTemplates.renderStaticSystemSkeleton(lang);
            List<TextBlockParam> systemBlocks = List.of(TextBlockParam.builder()
                    .text(systemSkeleton)
                    .cacheControl(CacheControlEphemeral.builder().ttl(CacheControlEphemeral.Ttl.TTL_1H).build())
                    .build());
            for (List<SourceFile> chunk : chunkFiles(langFiles, systemSkeleton)) {
                runChunk(job, chunk, systemBlocks);
            }
        }
    }

    private void runChunk(ExtractionJob job, List<SourceFile> chunk, List<TextBlockParam> systemBlocks) {
        Map<String, SourceFile> filesByCustomId = new HashMap<>();
        BatchCreateParams.Builder batchBuilder = BatchCreateParams.builder();
        for (int i = 0; i < chunk.size(); i++) {
            SourceFile file = chunk.get(i);
            String customId = "f" + i;
            filesByCustomId.put(customId, file);
            BatchCreateParams.Request.Params requestParams = BatchCreateParams.Request.Params.builder()
                    .model(batchProperties.model())
                    .maxTokens(batchProperties.maxTokens())
                    .temperature(batchProperties.temperature())
                    .systemOfTextBlockParams(systemBlocks)
                    .addUserMessage(promptTemplates.renderUserContent(file))
                    .build();
            batchBuilder.addRequest(BatchCreateParams.Request.builder()
                    .customId(customId)
                    .params(requestParams)
                    .build());
        }

        Set<String> seenCustomIds = new HashSet<>();
        String unresolvedReason = "No batch result returned";
        try {
            MessageBatch batch = anthropicClient.messages().batches().create(batchBuilder.build());
            String batchId = batch.id();
            long deadline = System.currentTimeMillis() + batchProperties.pollTimeout().toMillis();
            while (!batch.processingStatus().equals(MessageBatch.ProcessingStatus.ENDED)) {
                if (System.currentTimeMillis() > deadline) {
                    log.error("Job {}: batch {} timed out waiting for completion ({} files)",
                            job.id(), batchId, chunk.size());
                    failRemaining(job, filesByCustomId, seenCustomIds, "Batch processing timed out");
                    return;
                }
                sleep(batchProperties.pollInterval().toMillis());
                batch = anthropicClient.messages().batches().retrieve(batchId);
            }

            try (StreamResponse<MessageBatchIndividualResponse> results =
                         anthropicClient.messages().batches().resultsStreaming(batchId)) {
                results.stream().forEach(individual -> {
                    SourceFile file = filesByCustomId.get(individual.customId());
                    if (file == null) {
                        return;
                    }
                    seenCustomIds.add(individual.customId());
                    ExtractionResult result = toExtractionResult(file, individual.result());
                    resultWriter.write(job, result);
                    job.recordResult(result.success());
                });
            }
        } catch (Exception e) {
            log.error("Job {}: batch processing failed for a chunk of {} files: {}",
                    job.id(), chunk.size(), e.getMessage(), e);
            unresolvedReason = "Batch processing failed: " + e.getMessage();
        }

        // Covers both the exception path above and the (expected-rare) case where the API simply
        // never returned a result line for some custom_id — never double-counts an already-recorded file.
        failRemaining(job, filesByCustomId, seenCustomIds, unresolvedReason);
    }

    private void failRemaining(ExtractionJob job, Map<String, SourceFile> filesByCustomId,
                                Set<String> seenCustomIds, String reason) {
        for (Map.Entry<String, SourceFile> entry : filesByCustomId.entrySet()) {
            if (seenCustomIds.add(entry.getKey())) {
                resultWriter.write(job, ExtractionResult.failure(entry.getValue(), AGENT_NAME, reason, 0));
                job.recordResult(false);
            }
        }
    }

    private ExtractionResult toExtractionResult(SourceFile file, MessageBatchResult result) {
        if (result.isSucceeded()) {
            Message message = result.asSucceeded().message();
            String text = extractText(message);
            if (text == null) {
                return ExtractionResult.failure(file, AGENT_NAME, "No text content in batch response", 0);
            }
            Usage usage = message.usage();
            return ExtractionResult.success(file, AGENT_NAME, text, 0,
                    (int) usage.inputTokens(), (int) usage.outputTokens());
        }
        if (result.isErrored()) {
            return ExtractionResult.failure(file, AGENT_NAME, result.asErrored().error().error().toString(), 0);
        }
        if (result.isCanceled()) {
            return ExtractionResult.failure(file, AGENT_NAME, "Batch request canceled", 0);
        }
        return ExtractionResult.failure(file, AGENT_NAME, "Batch request expired", 0);
    }

    private String extractText(Message message) {
        for (ContentBlock block : message.content()) {
            if (block.isText()) {
                return block.asText().text();
            }
        }
        return null;
    }

    private List<List<SourceFile>> chunkFiles(List<SourceFile> files, String systemSkeleton) {
        long systemBytes = systemSkeleton.getBytes(StandardCharsets.UTF_8).length;
        List<List<SourceFile>> chunks = new ArrayList<>();
        List<SourceFile> current = new ArrayList<>();
        long currentBytes = 0;

        for (SourceFile file : files) {
            // The Batches API has no submission-time dedup: the cached system text still counts
            // against the 256MB request-body cap on every request line, even though Claude itself
            // only processes it once per cache write.
            long requestBytes = systemBytes + estimateUserContentBytes(file) + REQUEST_OVERHEAD_BYTES;
            boolean wouldExceedCount = current.size() + 1 > batchProperties.maxRequestsPerBatch();
            boolean wouldExceedBytes = currentBytes + requestBytes > batchProperties.maxBatchBytes();
            if (!current.isEmpty() && (wouldExceedCount || wouldExceedBytes)) {
                chunks.add(current);
                current = new ArrayList<>();
                currentBytes = 0;
            }
            current.add(file);
            currentBytes += requestBytes;
        }
        if (!current.isEmpty()) {
            chunks.add(current);
        }
        return chunks;
    }

    private long estimateUserContentBytes(SourceFile file) {
        return file.content().getBytes(StandardCharsets.UTF_8).length + file.relativePath().length() + 64;
    }

    private void sleep(long millis) {
        try {
            Thread.sleep(millis);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new RuntimeException("Interrupted while polling batch status", e);
        }
    }
}
