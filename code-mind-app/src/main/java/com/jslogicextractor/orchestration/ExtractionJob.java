package com.jslogicextractor.orchestration;

import java.nio.file.Path;
import java.time.Instant;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicInteger;

public final class ExtractionJob {

    private final UUID id;
    private final Path repositoryRoot;
    private final Path outputDirectory;
    private final int maxConcurrency;
    private final ExecutionMode executionMode;
    private final boolean incremental;
    private final Instant createdAt;

    private volatile JobPhase phase;
    private volatile Instant finishedAt;
    private volatile String failureReason;
    private volatile boolean cancelRequested;

    private final AtomicInteger totalFiles;
    private final AtomicInteger processedFiles;
    private final AtomicInteger succeededFiles;
    private final AtomicInteger failedFiles;
    private final AtomicInteger skippedFiles;

    public ExtractionJob(UUID id, Path repositoryRoot, Path outputDirectory, int maxConcurrency) {
        this(id, repositoryRoot, outputDirectory, maxConcurrency, null, false);
    }

    public ExtractionJob(UUID id, Path repositoryRoot, Path outputDirectory, int maxConcurrency,
                          ExecutionMode executionMode) {
        this(id, repositoryRoot, outputDirectory, maxConcurrency, executionMode, false);
    }

    public ExtractionJob(UUID id, Path repositoryRoot, Path outputDirectory, int maxConcurrency,
                          ExecutionMode executionMode, boolean incremental) {
        this.id = id;
        this.repositoryRoot = repositoryRoot;
        this.outputDirectory = outputDirectory;
        this.maxConcurrency = maxConcurrency;
        this.executionMode = executionMode != null ? executionMode : ExecutionMode.SYNC;
        this.incremental = incremental;
        this.createdAt = Instant.now();
        this.phase = JobPhase.PENDING;
        this.totalFiles = new AtomicInteger();
        this.processedFiles = new AtomicInteger();
        this.succeededFiles = new AtomicInteger();
        this.failedFiles = new AtomicInteger();
        this.skippedFiles = new AtomicInteger();
    }

    // For restoring from a persisted snapshot.
    ExtractionJob(UUID id, Path repositoryRoot, Path outputDirectory, int maxConcurrency,
                  ExecutionMode executionMode, boolean incremental,
                  Instant createdAt, JobPhase phase, Instant finishedAt, String failureReason,
                  int totalFiles, int processedFiles, int succeededFiles, int failedFiles, int skippedFiles) {
        this.id = id;
        this.repositoryRoot = repositoryRoot;
        this.outputDirectory = outputDirectory;
        this.maxConcurrency = maxConcurrency;
        this.executionMode = executionMode != null ? executionMode : ExecutionMode.SYNC;
        this.incremental = incremental;
        this.createdAt = createdAt;
        this.phase = phase;
        this.finishedAt = finishedAt;
        this.failureReason = failureReason;
        this.totalFiles = new AtomicInteger(totalFiles);
        this.processedFiles = new AtomicInteger(processedFiles);
        this.succeededFiles = new AtomicInteger(succeededFiles);
        this.failedFiles = new AtomicInteger(failedFiles);
        this.skippedFiles = new AtomicInteger(skippedFiles);
    }

    public void markScanning() { this.phase = JobPhase.SCANNING; }

    public void markFiltering(int total) {
        this.totalFiles.set(total);
        this.phase = JobPhase.FILTERING;
    }

    public void markProcessing() { this.phase = JobPhase.PROCESSING; }

    public void markCompleted() {
        this.phase = JobPhase.COMPLETED;
        this.finishedAt = Instant.now();
    }

    public void markCancelled() {
        this.phase = JobPhase.CANCELLED;
        this.finishedAt = Instant.now();
    }

    public void markFailed(String reason) {
        this.phase = JobPhase.FAILED;
        this.failureReason = reason;
        this.finishedAt = Instant.now();
    }

    public void requestCancel() { this.cancelRequested = true; }

    public boolean isCancelRequested() { return cancelRequested; }

    public void recordResult(boolean success) {
        processedFiles.incrementAndGet();
        if (success) succeededFiles.incrementAndGet();
        else failedFiles.incrementAndGet();
    }

    public void recordSkipped() {
        processedFiles.incrementAndGet();
        skippedFiles.incrementAndGet();
    }

    public JobSnapshot snapshot() {
        return new JobSnapshot(
                id,
                repositoryRoot.toString(),
                outputDirectory.toString(),
                maxConcurrency,
                executionMode.name(),
                incremental,
                phase.name(),
                createdAt,
                finishedAt,
                failureReason,
                totalFiles.get(),
                processedFiles.get(),
                succeededFiles.get(),
                failedFiles.get(),
                skippedFiles.get()
        );
    }

    public UUID id() { return id; }
    public Path repositoryRoot() { return repositoryRoot; }
    public Path outputDirectory() { return outputDirectory; }
    public int maxConcurrency() { return maxConcurrency; }
    public ExecutionMode executionMode() { return executionMode; }
    public boolean incremental() { return incremental; }
    public JobPhase phase() { return phase; }
    public Instant createdAt() { return createdAt; }
    public Instant finishedAt() { return finishedAt; }
    public String failureReason() { return failureReason; }
    public int totalCount() { return totalFiles.get(); }
    public int processedCount() { return processedFiles.get(); }
    public int succeededCount() { return succeededFiles.get(); }
    public int failedCount() { return failedFiles.get(); }
    public int skippedCount() { return skippedFiles.get(); }
}
