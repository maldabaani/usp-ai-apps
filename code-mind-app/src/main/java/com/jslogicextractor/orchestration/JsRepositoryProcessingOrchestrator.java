package com.jslogicextractor.orchestration;

import com.jslogicextractor.agent.AgentSelector;
import com.jslogicextractor.agent.ExtractionResult;
import com.jslogicextractor.agent.LogicExtractionAgent;
import com.jslogicextractor.batch.BatchExtractionService;
import com.jslogicextractor.config.ExtractionProperties;
import com.jslogicextractor.filter.NonSubstantiveFileFilter;
import com.jslogicextractor.incremental.ManifestService;
import com.jslogicextractor.output.ExtractionResultWriter;
import com.jslogicextractor.scanner.RepositoryScannerService;
import com.jslogicextractor.scanner.SourceFile;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.stream.Stream;

@Service
public class JsRepositoryProcessingOrchestrator {

    private static final Logger log = LoggerFactory.getLogger(JsRepositoryProcessingOrchestrator.class);
    private static final String PREFILTER_AGENT_NAME = "non-substantive-pre-filter";

    private final RepositoryScannerService scanner;
    private final AgentSelector agentSelector;
    private final ExtractionResultWriter resultWriter;
    private final NonSubstantiveFileFilter nonSubstantiveFileFilter;
    private final BatchExtractionService batchExtractionService;
    private final ManifestService manifestService;
    private final boolean skipExistingResults;

    public JsRepositoryProcessingOrchestrator(RepositoryScannerService scanner,
                                               AgentSelector agentSelector,
                                               ExtractionResultWriter resultWriter,
                                               NonSubstantiveFileFilter nonSubstantiveFileFilter,
                                               BatchExtractionService batchExtractionService,
                                               ExtractionProperties properties,
                                               ManifestService manifestService) {
        this.scanner = scanner;
        this.agentSelector = agentSelector;
        this.resultWriter = resultWriter;
        this.nonSubstantiveFileFilter = nonSubstantiveFileFilter;
        this.batchExtractionService = batchExtractionService;
        this.manifestService = manifestService;
        this.skipExistingResults = properties.skipExistingResults();
    }

    public void run(ExtractionJob job) {
        job.markScanning();
        List<SourceFile> files;
        try {
            files = Files.isRegularFile(job.repositoryRoot())
                    ? scanner.scanFile(job.repositoryRoot())
                    : scanner.scan(job.repositoryRoot());
        } catch (Exception e) {
            log.error("Repository scan failed for job {}: {}", job.id(), e.getMessage());
            job.markFailed("Repository scan failed: " + e.getMessage());
            resultWriter.writeSummary(job);
            return;
        }

        // For directory jobs: apply incremental filter if requested, otherwise use all scanned files.
        List<SourceFile> candidateFiles = files;
        if (job.incremental() && !Files.isRegularFile(job.repositoryRoot())) {
            candidateFiles = applyIncrementalFilter(job, files);
        }

        job.markFiltering(candidateFiles.size());
        log.info("Job {}: scanned {} files from {}, mode={}, incremental={}, maxConcurrency={}, agents={}",
                job.id(), files.size(), job.repositoryRoot(), job.executionMode(), job.incremental(),
                job.maxConcurrency(), agentSelector.agentCount());

        List<SourceFile> eligibleFiles = partitionEligibleFiles(job, candidateFiles);

        if (!eligibleFiles.isEmpty()) {
            job.markProcessing();
            if (job.executionMode() == ExecutionMode.BATCH) {
                runBatchMode(job, eligibleFiles);
            } else {
                runSyncFanOut(job, eligibleFiles);
            }
        }

        // Batch mode may have already moved the job to FAILED on a non-recoverable error (e.g. the
        // shared prompt skeleton failed to render before any file was submitted) — don't clobber that.
        if (job.phase() != JobPhase.FAILED) {
            if (job.isCancelRequested()) {
                job.markCancelled();
            } else {
                job.markCompleted();
            }
        }

        // Persist manifest only after a clean completion so future jobs can run incrementally.
        if (job.phase() == JobPhase.COMPLETED && !Files.isRegularFile(job.repositoryRoot())) {
            Map<String, String> hashes = manifestService.computeHashes(job.repositoryRoot(), files);
            manifestService.save(job.repositoryRoot(),
                    new ManifestService.Manifest(job.outputDirectory(), hashes));
        }

        resultWriter.writeSummary(job);
        log.info("Job {} finished: {} succeeded, {} failed, {} skipped, of {} files",
                job.id(), job.succeededCount(), job.failedCount(), job.skippedCount(), job.totalCount());
    }

    /**
     * Loads the manifest for this repo, diffs current hashes against it, removes output files for
     * deleted source files, and returns only the SourceFiles whose originals changed or were added.
     */
    private List<SourceFile> applyIncrementalFilter(ExtractionJob job, List<SourceFile> files) {
        Optional<ManifestService.Manifest> manifest = manifestService.load(job.repositoryRoot());
        if (manifest.isEmpty()) {
            // Manifest disappeared between JobStarter check and now — fall back to full run.
            return files;
        }

        Map<String, String> currentHashes = manifestService.computeHashes(job.repositoryRoot(), files);
        ManifestService.FileChanges changes = manifestService.diff(manifest.get().fileHashes(), currentHashes);

        for (String deletedRelPath : changes.deleted()) {
            deleteOutputFiles(job.outputDirectory(), deletedRelPath);
        }

        log.info("Job {} (incremental): {} added, {} modified, {} deleted of {} total files",
                job.id(), changes.added().size(), changes.modified().size(),
                changes.deleted().size(), files.size());

        Set<Path> changedAbsolutePaths = new HashSet<>();
        for (String relPath : changes.changedOrAdded()) {
            changedAbsolutePaths.add(job.repositoryRoot().resolve(relPath));
        }
        return files.stream()
                .filter(f -> changedAbsolutePaths.contains(f.absolutePath()))
                .toList();
    }

    private void deleteOutputFiles(Path outputDirectory, String relativeSourcePath) {
        Path directJson = outputDirectory.resolve(relativeSourcePath + ".json");
        try {
            Files.deleteIfExists(directJson);
        } catch (IOException e) {
            log.warn("Could not delete output file {}: {}", directJson, e.getMessage());
        }
        // Chunked files produce a sub-directory: outputDir/<relPath>/part-NNNN.ext.json
        Path chunkDir = outputDirectory.resolve(relativeSourcePath);
        if (Files.isDirectory(chunkDir)) {
            try (Stream<Path> walk = Files.walk(chunkDir)) {
                walk.sorted(Comparator.reverseOrder())
                        .forEach(p -> { try { Files.delete(p); } catch (IOException ignored) {} });
            } catch (IOException e) {
                log.warn("Could not remove chunk directory {}: {}", chunkDir, e.getMessage());
            }
        }
    }

    /**
     * Applies the unconditional non-substantive pre-filter and (for sync mode's resumable re-run
     * support) the existing-result skip, regardless of which execution mode handles the remainder.
     */
    private List<SourceFile> partitionEligibleFiles(ExtractionJob job, List<SourceFile> files) {
        List<SourceFile> eligibleFiles = new ArrayList<>();
        for (SourceFile file : files) {
            Optional<String> skipReason = nonSubstantiveFileFilter.skipReason(file);
            if (skipReason.isPresent()) {
                resultWriter.write(job, ExtractionResult.skipped(file, PREFILTER_AGENT_NAME, skipReason.get()));
                job.recordSkipped();
                continue;
            }
            if (skipExistingResults && resultWriter.exists(job, file.relativePath())) {
                job.recordResult(true);
                continue;
            }
            eligibleFiles.add(file);
        }
        return eligibleFiles;
    }

    private void runSyncFanOut(ExtractionJob job, List<SourceFile> files) {
        // Platform threads (no virtual threads pre-JDK21): pool size itself is the concurrency throttle.
        ExecutorService fileExecutor = Executors.newFixedThreadPool(job.maxConcurrency());
        try {
            List<CompletableFuture<Void>> futures = files.stream()
                    .map(file -> CompletableFuture.runAsync(() -> processFile(job, file), fileExecutor))
                    .toList();
            CompletableFuture.allOf(futures.toArray(CompletableFuture[]::new)).join();
        } finally {
            fileExecutor.shutdown();
        }
    }

    private void processFile(ExtractionJob job, SourceFile file) {
        if (job.isCancelRequested()) return;
        try {
            LogicExtractionAgent agent = agentSelector.next();
            ExtractionResult result = agent.extract(file);
            resultWriter.write(job, result);
            job.recordResult(result.success());
        } catch (Exception e) {
            log.error("Unexpected error processing {}: {}", file.relativePath(), e.getMessage(), e);
            job.recordResult(false);
        }
    }

    private void runBatchMode(ExtractionJob job, List<SourceFile> files) {
        try {
            batchExtractionService.runBatch(job, files);
        } catch (Exception e) {
            log.error("Job {}: batch execution failed: {}", job.id(), e.getMessage(), e);
            job.markFailed("Batch execution failed: " + e.getMessage());
        }
    }
}
