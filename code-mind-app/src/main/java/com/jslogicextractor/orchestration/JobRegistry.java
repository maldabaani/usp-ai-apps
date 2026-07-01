package com.jslogicextractor.orchestration;

import com.jslogicextractor.config.ExtractionProperties;
import jakarta.annotation.PostConstruct;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.util.Comparator;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.stream.Stream;

@Component
public class JobRegistry {

    private static final Logger log = LoggerFactory.getLogger(JobRegistry.class);

    private final Map<UUID, ExtractionJob> jobs = new ConcurrentHashMap<>();
    private final ExtractionProperties defaults;
    private final JobStore jobStore;

    public JobRegistry(ExtractionProperties defaults, JobStore jobStore) {
        this.defaults = defaults;
        this.jobStore = jobStore;
    }

    @PostConstruct
    void loadPersistedJobs() {
        List<JobSnapshot> snapshots = jobStore.loadAll();
        log.info("Loaded {} persisted job(s) from store", snapshots.size());
        for (JobSnapshot s : snapshots) {
            boolean terminal = s.phase().equals("COMPLETED") || s.phase().equals("FAILED") || s.phase().equals("CANCELLED");
            JobPhase restoredPhase = terminal ? JobPhase.valueOf(s.phase()) : JobPhase.FAILED;
            String restoredReason = (!terminal) ? "Interrupted at server restart" : s.failureReason();
            Instant restoredFinishedAt = (!terminal && s.finishedAt() == null) ? Instant.now() : s.finishedAt();

            ExtractionJob job = new ExtractionJob(
                    s.id(),
                    Path.of(s.repositoryRoot()),
                    Path.of(s.outputDirectory()),
                    s.maxConcurrency(),
                    ExecutionMode.valueOf(s.executionMode()),
                    s.incremental(),
                    s.createdAt(),
                    restoredPhase,
                    restoredFinishedAt,
                    restoredReason,
                    s.totalFiles(),
                    s.processedFiles(),
                    s.succeededFiles(),
                    s.failedFiles(),
                    s.skippedFiles()
            );
            jobs.put(job.id(), job);
        }
    }

    public ExtractionJob register(Path repositoryRoot, Path outputDirectoryOverride, Integer maxConcurrencyOverride,
                                   ExecutionMode executionModeOverride) {
        return register(repositoryRoot, outputDirectoryOverride, maxConcurrencyOverride, executionModeOverride, false);
    }

    public ExtractionJob register(Path repositoryRoot, Path outputDirectoryOverride, Integer maxConcurrencyOverride,
                                   ExecutionMode executionModeOverride, boolean incremental) {
        UUID id = UUID.randomUUID();
        Path outputDirectory = outputDirectoryOverride != null
                ? outputDirectoryOverride
                : defaults.defaultOutputDirectory().resolve(id.toString());
        int maxConcurrency = maxConcurrencyOverride != null ? maxConcurrencyOverride : defaults.maxConcurrentRequests();
        ExecutionMode executionMode = executionModeOverride != null ? executionModeOverride : defaults.executionMode();

        ExtractionJob job = new ExtractionJob(id, repositoryRoot, outputDirectory, maxConcurrency, executionMode, incremental);
        jobs.put(id, job);
        jobStore.save(job.snapshot());
        return job;
    }

    public void persist(ExtractionJob job) {
        jobStore.save(job.snapshot());
    }

    public Optional<ExtractionJob> find(UUID id) {
        return Optional.ofNullable(jobs.get(id));
    }

    public List<ExtractionJob> findAll() {
        return jobs.values().stream()
                .sorted(Comparator.comparing(ExtractionJob::createdAt).reversed())
                .toList();
    }

    public void delete(UUID id) {
        ExtractionJob job = jobs.remove(id);
        if (job != null) {
            deleteDirectory(job.outputDirectory());
        }
        jobStore.delete(id);
        log.info("Deleted job {}", id);
    }

    public void clearAll() {
        jobs.values().forEach(job -> deleteDirectory(job.outputDirectory()));
        deleteDirectory(defaults.defaultOutputDirectory().toAbsolutePath().resolve(".manifests"));
        jobStore.deleteAll();
        jobs.clear();
        log.info("All job data cleared");
    }

    private void deleteDirectory(Path dir) {
        if (!Files.exists(dir)) return;
        try (Stream<Path> walk = Files.walk(dir)) {
            walk.sorted(Comparator.reverseOrder()).forEach(p -> {
                try { Files.deleteIfExists(p); } catch (IOException ignored) {}
            });
        } catch (IOException e) {
            log.warn("Failed to delete directory {}: {}", dir, e.getMessage());
        }
    }
}
