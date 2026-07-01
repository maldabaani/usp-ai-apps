package com.jslogicextractor.output;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.jslogicextractor.orchestration.ExtractionJob;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.util.Comparator;
import java.util.List;
import java.util.Objects;
import java.util.Optional;
import java.util.stream.Stream;

/**
 * Lists files that have landed under a job's output directory, newest first. Backs the progress
 * UI's "files appearing as they're written" feed, polled rather than watched via {@code WatchService}.
 */
@Component
public class OutputFileSnapshotService {

    private static final Logger log = LoggerFactory.getLogger(OutputFileSnapshotService.class);
    private static final String SUMMARY_FILE_NAME = "_summary.json";

    private final ObjectMapper objectMapper;

    public OutputFileSnapshotService(ObjectMapper objectMapper) {
        this.objectMapper = objectMapper;
    }

    public List<OutputFile> recentFiles(ExtractionJob job, int limit) {
        Path outputDirectory = job.outputDirectory();
        if (!Files.isDirectory(outputDirectory)) {
            // Output dir is created lazily on first write; a job still scanning/filtering has none yet.
            return List.of();
        }
        try (Stream<Path> paths = Files.walk(outputDirectory)) {
            return paths.filter(Files::isRegularFile)
                    .filter(path -> path.getFileName().toString().endsWith(".json"))
                    .filter(path -> !path.getFileName().toString().equals(SUMMARY_FILE_NAME))
                    .map(path -> toOutputFile(outputDirectory, path))
                    .filter(Objects::nonNull)
                    .sorted(Comparator.comparing(OutputFile::modifiedAt).reversed())
                    .limit(limit)
                    .toList();
        } catch (IOException e) {
            log.warn("Failed to list output files for job {}: {}", job.id(), e.getMessage());
            return List.of();
        }
    }

    /** Returns the raw JSON content of a single output file, guarded against path traversal. */
    public Optional<String> readOutputFile(ExtractionJob job, String relativePath) {
        Path file = job.outputDirectory().resolve(relativePath).normalize();
        if (!file.startsWith(job.outputDirectory().normalize())) {
            return Optional.empty();
        }
        try {
            return Optional.of(Files.readString(file, StandardCharsets.UTF_8));
        } catch (IOException e) {
            return Optional.empty();
        }
    }

    /** Scans all output files and returns those where {@code success=false}. */
    public List<FailedFile> listFailedFiles(ExtractionJob job) {
        Path outputDirectory = job.outputDirectory();
        if (!Files.isDirectory(outputDirectory)) return List.of();
        try (Stream<Path> paths = Files.walk(outputDirectory)) {
            return paths
                    .filter(Files::isRegularFile)
                    .filter(p -> p.getFileName().toString().endsWith(".json"))
                    .filter(p -> !p.getFileName().toString().equals(SUMMARY_FILE_NAME))
                    .map(p -> tryReadFailedFile(outputDirectory, p))
                    .filter(Objects::nonNull)
                    .sorted(Comparator.comparing(FailedFile::relativePath))
                    .toList();
        } catch (IOException e) {
            log.warn("Failed to list failed files for job {}: {}", job.id(), e.getMessage());
            return List.of();
        }
    }

    private FailedFile tryReadFailedFile(Path outputDirectory, Path path) {
        try {
            JsonNode node = objectMapper.readTree(path.toFile());
            if (node.path("success").asBoolean(true)) return null;
            String rel = outputDirectory.relativize(path).toString().replace('\\', '/');
            if (rel.endsWith(".json")) rel = rel.substring(0, rel.length() - 5);
            return new FailedFile(
                    rel,
                    node.path("errorMessage").asText("Unknown error"),
                    node.path("durationMillis").asLong(0)
            );
        } catch (IOException e) {
            return null;
        }
    }

    private OutputFile toOutputFile(Path outputDirectory, Path path) {
        try {
            String relativePath = outputDirectory.relativize(path).toString().replace('\\', '/');
            Instant modifiedAt = Files.getLastModifiedTime(path).toInstant();
            long sizeBytes = Files.size(path);
            return new OutputFile(relativePath, sizeBytes, modifiedAt);
        } catch (IOException e) {
            // The writer may still be mid-write or the file may have been replaced; skip it this poll.
            return null;
        }
    }

    public record OutputFile(String relativePath, long sizeBytes, Instant modifiedAt) {}

    public record FailedFile(String relativePath, String errorMessage, long durationMillis) {}
}
