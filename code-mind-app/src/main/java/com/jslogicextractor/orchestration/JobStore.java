package com.jslogicextractor.orchestration;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import java.util.stream.Stream;

@Component
class JobStore {

    private static final Logger log = LoggerFactory.getLogger(JobStore.class);

    private final Path storeDirectory;
    private final ObjectMapper objectMapper;

    JobStore(@Value("${jsprocessor.job-store-directory:}") String dirFromConfig,
             ObjectMapper objectMapper) {
        this.storeDirectory = dirFromConfig.isBlank()
                ? Path.of(System.getProperty("user.home"), ".js-logic-extractor", "jobs")
                : Path.of(dirFromConfig);
        this.objectMapper = objectMapper;
    }

    void save(JobSnapshot snapshot) {
        try {
            Files.createDirectories(storeDirectory);
            Path file = storeDirectory.resolve(snapshot.id() + ".json");
            objectMapper.writeValue(file.toFile(), snapshot);
        } catch (IOException e) {
            log.warn("Failed to persist job {}: {}", snapshot.id(), e.getMessage());
        }
    }

    List<JobSnapshot> loadAll() {
        if (!Files.isDirectory(storeDirectory)) {
            return List.of();
        }
        try (Stream<Path> files = Files.list(storeDirectory)) {
            return files
                    .filter(p -> p.getFileName().toString().endsWith(".json"))
                    .map(this::loadSnapshot)
                    .filter(Optional::isPresent)
                    .map(Optional::get)
                    .toList();
        } catch (IOException e) {
            log.warn("Failed to list job store directory {}: {}", storeDirectory, e.getMessage());
            return List.of();
        }
    }

    void delete(UUID id) {
        Path file = storeDirectory.resolve(id + ".json");
        try {
            Files.deleteIfExists(file);
        } catch (IOException e) {
            log.warn("Failed to delete job file {}: {}", file.getFileName(), e.getMessage());
        }
    }

    void deleteAll() {
        if (!Files.isDirectory(storeDirectory)) return;
        try (Stream<Path> files = Files.list(storeDirectory)) {
            files.filter(p -> p.getFileName().toString().endsWith(".json"))
                    .forEach(p -> {
                        try { Files.deleteIfExists(p); } catch (IOException e) {
                            log.warn("Failed to delete job file {}: {}", p.getFileName(), e.getMessage());
                        }
                    });
        } catch (IOException e) {
            log.warn("Failed to list job store directory for deletion {}: {}", storeDirectory, e.getMessage());
        }
    }

    private Optional<JobSnapshot> loadSnapshot(Path file) {
        try {
            return Optional.of(objectMapper.readValue(file.toFile(), JobSnapshot.class));
        } catch (IOException e) {
            log.warn("Failed to load job snapshot from {}: {}", file.getFileName(), e.getMessage());
            return Optional.empty();
        }
    }
}
