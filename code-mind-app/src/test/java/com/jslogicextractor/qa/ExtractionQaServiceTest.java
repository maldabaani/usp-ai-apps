package com.jslogicextractor.qa;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.jslogicextractor.agent.ExtractionResult;
import com.jslogicextractor.orchestration.ExtractionJob;
import com.jslogicextractor.scanner.SourceFile;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.mockito.ArgumentMatchers;
import org.springframework.ai.chat.client.ChatClient;
import org.springframework.ai.document.Document;
import org.springframework.ai.embedding.EmbeddingModel;
import reactor.core.publisher.Flux;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Optional;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.atLeastOnce;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class ExtractionQaServiceTest {

    @TempDir
    Path outputDirectory;

    private final ObjectMapper objectMapper = new ObjectMapper();

    @Test
    void answersUsingTopScoringFilesAsContextViaKeywordFallback() throws IOException {
        writeResult("auth.js.json", "auth.js", "Checks password and creates session for login users.");
        writeResult("payments.js.json", "payments.js", "Charges a credit card via the Stripe API.");
        writeResult("_summary.json", null, null);

        ExtractionQaService service = new ExtractionQaService(objectMapper,
                chatClientReturning("It checks the password and creates a session."), Optional.empty());
        ExtractionJob job = new ExtractionJob(UUID.randomUUID(), outputDirectory, outputDirectory, 4);

        QaAnswer answer = service.ask(job, "how does login check the password and session work?");

        assertThat(answer.answer()).isEqualTo("It checks the password and creates a session.");
        assertThat(answer.sourceFiles()).containsExactly("auth.js");
    }

    @Test
    void answersUsingVectorSearchWhenEmbeddingModelIsPresent() throws IOException {
        writeResult("auth.js.json", "auth.js", "Checks password and creates session for login users.");
        writeResult("payments.js.json", "payments.js", "Charges a credit card via the Stripe API.");

        EmbeddingModel embeddingModel = mock(EmbeddingModel.class);
        when(embeddingModel.embed(ArgumentMatchers.<Document>argThat(
                doc -> doc != null && "auth.js".equals(doc.getMetadata().get("relativePath")))))
                .thenReturn(new float[]{1f, 0f});
        when(embeddingModel.embed(ArgumentMatchers.<Document>argThat(
                doc -> doc != null && "payments.js".equals(doc.getMetadata().get("relativePath")))))
                .thenReturn(new float[]{0f, 1f});
        when(embeddingModel.embed(any(String.class))).thenReturn(new float[]{1f, 0f});

        ExtractionQaService service = new ExtractionQaService(objectMapper,
                chatClientReturning("Vector-grounded answer."), Optional.of(embeddingModel));
        ExtractionJob job = new ExtractionJob(UUID.randomUUID(), outputDirectory, outputDirectory, 4);

        QaAnswer answer = service.ask(job, "how does login work?");

        assertThat(answer.answer()).isEqualTo("Vector-grounded answer.");
        assertThat(answer.sourceFiles()).startsWith("auth.js");
        verify(embeddingModel, atLeastOnce()).embed(any(Document.class));
        verify(embeddingModel).embed(any(String.class));
    }

    @Test
    void fallsBackToKeywordSearchWhenEmbeddingCallFails() throws IOException {
        writeResult("auth.js.json", "auth.js", "Checks password and creates session for login users.");
        writeResult("payments.js.json", "payments.js", "Charges a credit card via the Stripe API.");

        EmbeddingModel embeddingModel = mock(EmbeddingModel.class);
        when(embeddingModel.embed(any(Document.class))).thenThrow(new RuntimeException("ollama unreachable"));

        ExtractionQaService service = new ExtractionQaService(objectMapper,
                chatClientReturning("It checks the password and creates a session."), Optional.of(embeddingModel));
        ExtractionJob job = new ExtractionJob(UUID.randomUUID(), outputDirectory, outputDirectory, 4);

        QaAnswer answer = service.ask(job, "how does login check the password and session work?");

        assertThat(answer.answer()).isEqualTo("It checks the password and creates a session.");
        assertThat(answer.sourceFiles()).containsExactly("auth.js");
    }

    @Test
    void returnsPlaceholderWhenNoResultsExistYet() {
        ExtractionQaService service = new ExtractionQaService(objectMapper,
                mock(ChatClient.class), Optional.empty());
        ExtractionJob job = new ExtractionJob(UUID.randomUUID(), outputDirectory, outputDirectory.resolve("missing"), 4);

        QaAnswer answer = service.ask(job, "anything?");

        assertThat(answer.sourceFiles()).isEmpty();
        assertThat(answer.answer()).contains("No extraction results");
    }

    @Test
    void returnsPlaceholderWhenNothingMatchesTheQuestion() throws IOException {
        writeResult("payments.js.json", "payments.js", "Charges a credit card via the Stripe API.");

        ExtractionQaService service = new ExtractionQaService(objectMapper,
                mock(ChatClient.class), Optional.empty());
        ExtractionJob job = new ExtractionJob(UUID.randomUUID(), outputDirectory, outputDirectory, 4);

        QaAnswer answer = service.ask(job, "xyzxyz nonsense qqq");

        assertThat(answer.sourceFiles()).isEmpty();
        assertThat(answer.answer()).contains("None of the");
    }

    @Test
    void askForStreamReturnsSourceFilesAndTextFluxViaKeywordFallback() throws IOException {
        writeResult("auth.js.json", "auth.js", "Checks password and creates session for login users.");

        Flux<String> stream = Flux.just("It ", "checks ", "the ", "password.");
        ExtractionQaService service = new ExtractionQaService(objectMapper,
                chatClientReturningStream(stream), Optional.empty());
        ExtractionJob job = new ExtractionJob(UUID.randomUUID(), outputDirectory, outputDirectory, 4);

        ExtractionQaService.QaStreamResult result = service.askForStream(job, "how does login work?");

        assertThat(result.sourceFiles()).containsExactly("auth.js");
        assertThat(result.textFlux()).isSameAs(stream);
    }

    @Test
    void askForStreamReturnsFallbackFluxWhenNoResultsExist() {
        ExtractionQaService service = new ExtractionQaService(objectMapper,
                mock(ChatClient.class), Optional.empty());
        ExtractionJob job = new ExtractionJob(UUID.randomUUID(), outputDirectory, outputDirectory.resolve("missing"), 4);

        ExtractionQaService.QaStreamResult result = service.askForStream(job, "anything?");

        assertThat(result.sourceFiles()).isEmpty();
        String text = result.textFlux().reduce("", String::concat).block();
        assertThat(text).contains("No extraction results");
    }

    private ChatClient chatClientReturning(String content) {
        ChatClient chatClient = mock(ChatClient.class);
        ChatClient.ChatClientRequestSpec requestSpec = mock(ChatClient.ChatClientRequestSpec.class);
        ChatClient.CallResponseSpec callResponseSpec = mock(ChatClient.CallResponseSpec.class);
        when(chatClient.prompt()).thenReturn(requestSpec);
        when(requestSpec.system(any(String.class))).thenReturn(requestSpec);
        when(requestSpec.user(any(String.class))).thenReturn(requestSpec);
        when(requestSpec.call()).thenReturn(callResponseSpec);
        when(callResponseSpec.content()).thenReturn(content);
        return chatClient;
    }

    private ChatClient chatClientReturningStream(Flux<String> stream) {
        ChatClient chatClient = mock(ChatClient.class);
        ChatClient.ChatClientRequestSpec requestSpec = mock(ChatClient.ChatClientRequestSpec.class);
        ChatClient.StreamResponseSpec streamResponseSpec = mock(ChatClient.StreamResponseSpec.class);
        when(chatClient.prompt()).thenReturn(requestSpec);
        when(requestSpec.system(any(String.class))).thenReturn(requestSpec);
        when(requestSpec.user(any(String.class))).thenReturn(requestSpec);
        when(requestSpec.stream()).thenReturn(streamResponseSpec);
        when(streamResponseSpec.content()).thenReturn(stream);
        return chatClient;
    }

    private void writeResult(String fileName, String relativePath, String content) throws IOException {
        Object payload = relativePath == null
                ? new java.util.LinkedHashMap<String, Object>()
                : ExtractionResult.success(sourceFile(relativePath), "test-agent", content, 1, null, null);
        Files.writeString(outputDirectory.resolve(fileName), objectMapper.writeValueAsString(payload),
                StandardCharsets.UTF_8);
    }

    private SourceFile sourceFile(String relativePath) {
        return new SourceFile(outputDirectory.resolve(relativePath), relativePath, "", 0);
    }
}
