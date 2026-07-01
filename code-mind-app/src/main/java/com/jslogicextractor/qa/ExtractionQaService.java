package com.jslogicextractor.qa;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.jslogicextractor.agent.ExtractionResult;
import com.jslogicextractor.orchestration.ExtractionJob;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.ai.chat.client.ChatClient;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.ai.document.Document;
import org.springframework.ai.embedding.EmbeddingModel;
import org.springframework.ai.vectorstore.SearchRequest;
import org.springframework.ai.vectorstore.SimpleVectorStore;
import org.springframework.ai.vectorstore.VectorStore;
import org.springframework.stereotype.Service;
import reactor.core.publisher.Flux;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Comparator;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Objects;
import java.util.Optional;
import java.util.Set;
import java.util.function.Function;
import java.util.regex.MatchResult;
import java.util.regex.Pattern;
import java.util.stream.Collectors;
import java.util.stream.Stream;

/**
 * RAG over a job's already-written extraction results: retrieves the files most relevant to the
 * question, feeds them to Claude as grounded context, and returns the answer plus the source files
 * it drew from. Retrieval is real vector search (embeddings + cosine similarity via an ephemeral
 * SimpleVectorStore) whenever an EmbeddingModel bean exists (jsprocessor.embedding.enabled=true);
 * otherwise — or if an embedding call fails, e.g. the local Ollama daemon is unreachable — it falls
 * back to keyword-overlap scoring so the endpoint keeps working with zero extra infrastructure.
 */
@Service
public class ExtractionQaService {

    private static final Logger log = LoggerFactory.getLogger(ExtractionQaService.class);
    private static final String SUMMARY_FILE_NAME = "_summary.json";
    private static final String RELATIVE_PATH_METADATA_KEY = "relativePath";
    private static final Pattern WORD_PATTERN = Pattern.compile("[A-Za-z0-9_]+");
    private static final Set<String> STOPWORDS = Set.of(
            "the", "is", "are", "what", "how", "does", "this", "that", "with", "for", "and", "where",
            "which", "who", "why", "when", "did", "can", "could", "would", "should", "to", "of", "in",
            "on", "a", "an", "it", "do", "be", "i", "you", "we");
    private static final int TOP_K = 6;
    private static final int MAX_CONTENT_CHARS_PER_FILE = 3000;
    private static final String SYSTEM_PROMPT_TEMPLATE = """
            You are answering questions about a codebase using only the extracted logic summaries
            provided below as context. Each summary is labeled with its source file path. Ground your
            answer strictly in this context; if the context doesn't contain the answer, say so
            explicitly rather than guessing. Cite the relevant file path(s) inline when you reference
            specific logic.

            Context:
            %s
            """;

    private final ObjectMapper objectMapper;
    private final ChatClient chatClient;
    private final Optional<EmbeddingModel> embeddingModel;

    public ExtractionQaService(ObjectMapper objectMapper,
                                @Qualifier("qaChatClient") ChatClient chatClient,
                                Optional<EmbeddingModel> embeddingModel) {
        this.objectMapper = objectMapper;
        this.chatClient = chatClient;
        this.embeddingModel = embeddingModel;
    }

    public record QaStreamResult(List<String> sourceFiles, Flux<String> textFlux) {}

    public QaStreamResult askForStream(ExtractionJob job, String question) {
        return askForStream(List.of(job), question);
    }

    public QaStreamResult askForStream(List<ExtractionJob> jobs, String question) {
        List<ExtractionResult> results = jobs.stream()
                .flatMap(job -> loadResults(job.outputDirectory()).stream())
                .toList();
        if (results.isEmpty()) {
            return new QaStreamResult(List.of(),
                    Flux.just("No extraction results are available yet. "
                            + "Wait for jobs to finish processing, then ask again."));
        }

        List<ScoredResult> ranked = retrieve(question, results);
        if (ranked.isEmpty()) {
            return new QaStreamResult(List.of(),
                    Flux.just("None of the " + results.size()
                            + " extracted files were relevant enough to answer that question confidently."));
        }

        List<String> sourceFiles = ranked.stream().map(s -> s.result().relativePath()).toList();
        String systemPrompt = SYSTEM_PROMPT_TEMPLATE.formatted(buildContext(ranked));
        Flux<String> textFlux = chatClient.prompt()
                .system(systemPrompt)
                .user(question)
                .stream()
                .content();
        return new QaStreamResult(sourceFiles, textFlux);
    }

    public QaAnswer ask(ExtractionJob job, String question) {
        List<ExtractionResult> results = loadResults(job.outputDirectory());
        if (results.isEmpty()) {
            return new QaAnswer(
                    "No extraction results are available yet for this job. Wait for files to finish "
                            + "processing, then ask again.",
                    List.of());
        }

        List<ScoredResult> ranked = retrieve(question, results);
        if (ranked.isEmpty()) {
            return new QaAnswer(
                    "None of the " + results.size()
                            + " extracted files were relevant enough to answer that question confidently.",
                    List.of());
        }

        String answer = callClaude(question, buildContext(ranked));
        List<String> sourceFiles = ranked.stream().map(scored -> scored.result().relativePath()).toList();
        return new QaAnswer(answer, sourceFiles);
    }

    private List<ScoredResult> retrieve(String question, List<ExtractionResult> results) {
        if (embeddingModel.isPresent()) {
            try {
                return rankByVectorSearch(question, results, embeddingModel.get());
            } catch (Exception e) {
                log.warn("Vector search failed ({}); falling back to keyword search", e.getMessage());
            }
        }
        return rankByKeywordOverlap(question, results);
    }

    private List<ScoredResult> rankByVectorSearch(String question, List<ExtractionResult> results,
                                                   EmbeddingModel model) {
        Map<String, ExtractionResult> byPath = results.stream()
                .collect(Collectors.toMap(ExtractionResult::relativePath, Function.identity(), (a, b) -> a));
        List<Document> documents = results.stream()
                .map(result -> new Document(truncate(result.content()),
                        Map.of(RELATIVE_PATH_METADATA_KEY, result.relativePath())))
                .toList();

        VectorStore vectorStore = SimpleVectorStore.builder(model).build();
        vectorStore.add(documents);
        List<Document> matches = vectorStore.similaritySearch(
                SearchRequest.builder().query(question).topK(TOP_K).build());

        return matches.stream()
                .map(doc -> byPath.get((String) doc.getMetadata().get(RELATIVE_PATH_METADATA_KEY)))
                .filter(Objects::nonNull)
                .map(result -> new ScoredResult(result, 1))
                .toList();
    }

    private List<ScoredResult> rankByKeywordOverlap(String question, List<ExtractionResult> results) {
        Set<String> queryTerms = tokenize(question);
        return results.stream()
                .map(result -> new ScoredResult(result, score(queryTerms, result)))
                .filter(scored -> scored.score() > 0)
                .sorted(Comparator.comparingInt(ScoredResult::score).reversed())
                .limit(TOP_K)
                .toList();
    }

    private String truncate(String content) {
        if (content.length() <= MAX_CONTENT_CHARS_PER_FILE) {
            return content;
        }
        return content.substring(0, MAX_CONTENT_CHARS_PER_FILE);
    }

    private List<ExtractionResult> loadResults(Path outputDirectory) {
        if (!Files.isDirectory(outputDirectory)) {
            return List.of();
        }
        try (Stream<Path> paths = Files.walk(outputDirectory)) {
            return paths.filter(Files::isRegularFile)
                    .filter(path -> path.getFileName().toString().endsWith(".json"))
                    .filter(path -> !path.getFileName().toString().equals(SUMMARY_FILE_NAME))
                    .map(this::readResult)
                    .filter(Objects::nonNull)
                    .filter(this::hasUsableContent)
                    .toList();
        } catch (IOException e) {
            log.warn("Failed to load extraction results from {}: {}", outputDirectory, e.getMessage());
            return List.of();
        }
    }

    private boolean hasUsableContent(ExtractionResult result) {
        return result.success() && !result.skipped() && result.content() != null && !result.content().isBlank();
    }

    private ExtractionResult readResult(Path path) {
        try {
            return objectMapper.readValue(path.toFile(), ExtractionResult.class);
        } catch (IOException e) {
            return null;
        }
    }

    private Set<String> tokenize(String text) {
        return WORD_PATTERN.matcher(text.toLowerCase(Locale.ROOT))
                .results()
                .map(MatchResult::group)
                .filter(word -> word.length() > 2 && !STOPWORDS.contains(word))
                .collect(Collectors.toSet());
    }

    private int score(Set<String> queryTerms, ExtractionResult result) {
        if (queryTerms.isEmpty()) {
            return 0;
        }
        String contentLower = result.content().toLowerCase(Locale.ROOT);
        String pathLower = result.relativePath().toLowerCase(Locale.ROOT);
        int score = 0;
        for (String term : queryTerms) {
            if (pathLower.contains(term)) {
                score += 3;
            }
            if (contentLower.contains(term)) {
                score += 1;
            }
        }
        return score;
    }

    private String buildContext(List<ScoredResult> ranked) {
        StringBuilder builder = new StringBuilder();
        for (ScoredResult scored : ranked) {
            ExtractionResult result = scored.result();
            String content = result.content();
            if (content.length() > MAX_CONTENT_CHARS_PER_FILE) {
                content = content.substring(0, MAX_CONTENT_CHARS_PER_FILE) + "... [truncated]";
            }
            builder.append("File: ").append(result.relativePath()).append('\n')
                    .append(content).append("\n\n---\n\n");
        }
        return builder.toString();
    }

    private String callClaude(String question, String context) {
        String systemPrompt = SYSTEM_PROMPT_TEMPLATE.formatted(context);
        return chatClient.prompt()
                .system(systemPrompt)
                .user(question)
                .call()
                .content();
    }

    private record ScoredResult(ExtractionResult result, int score) {
    }
}
