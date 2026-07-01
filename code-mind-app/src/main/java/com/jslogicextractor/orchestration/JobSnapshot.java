package com.jslogicextractor.orchestration;

import java.time.Instant;
import java.util.UUID;

public record JobSnapshot(
        UUID id,
        String repositoryRoot,
        String outputDirectory,
        int maxConcurrency,
        String executionMode,
        boolean incremental,
        String phase,
        Instant createdAt,
        Instant finishedAt,
        String failureReason,
        int totalFiles,
        int processedFiles,
        int succeededFiles,
        int failedFiles,
        int skippedFiles
) {}
