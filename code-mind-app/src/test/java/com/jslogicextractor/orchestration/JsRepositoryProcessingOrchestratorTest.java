package com.jslogicextractor.orchestration;

import com.jslogicextractor.agent.AgentSelector;
import com.jslogicextractor.agent.ExtractionResult;
import com.jslogicextractor.agent.LogicExtractionAgent;
import com.jslogicextractor.batch.BatchExtractionService;
import com.jslogicextractor.config.ChunkingProperties;
import com.jslogicextractor.config.ExtractionProperties;
import com.jslogicextractor.filter.NonSubstantiveFileFilter;
import com.jslogicextractor.incremental.ManifestService;
import com.jslogicextractor.output.ExtractionResultWriter;
import com.jslogicextractor.scanner.LargeFileChunker;
import com.jslogicextractor.scanner.RepositoryScannerService;
import com.jslogicextractor.scanner.SourceFile;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.atomic.AtomicInteger;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class JsRepositoryProcessingOrchestratorTest {

    @TempDir
    Path repoRoot;

    @Test
    void processesAllFilesWithBoundedConcurrencyAndIsolatesFailures() throws IOException {
        write(repoRoot.resolve("a.js"), "const a = 1;");
        write(repoRoot.resolve("b.js"), "const b = 2;");
        write(repoRoot.resolve("c.js"), "const c = 3;");

        ExtractionProperties properties = new ExtractionProperties(null, null, null, 300_000, 8, false, null);
        ChunkingProperties chunkingProperties = new ChunkingProperties(false, 0);
        RepositoryScannerService scanner = new RepositoryScannerService(properties, chunkingProperties, new LargeFileChunker(chunkingProperties));

        AtomicInteger activeCalls = new AtomicInteger();
        AtomicInteger maxObservedConcurrency = new AtomicInteger();
        int concurrencyLimit = 2;

        LogicExtractionAgent agent = new LogicExtractionAgent() {
            @Override
            public String name() {
                return "test-agent";
            }

            @Override
            public ExtractionResult extract(SourceFile file) {
                int current = activeCalls.incrementAndGet();
                maxObservedConcurrency.updateAndGet(prev -> Math.max(prev, current));
                try {
                    Thread.sleep(50);
                    if (file.relativePath().equals("b.js")) {
                        throw new RuntimeException("boom");
                    }
                    return ExtractionResult.success(file, name(), "{}", 1, null, null);
                } catch (InterruptedException e) {
                    throw new RuntimeException(e);
                } finally {
                    activeCalls.decrementAndGet();
                }
            }
        };

        AgentSelector selector = new AgentSelector(List.of(agent));
        List<ExtractionResult> writtenResults = new CopyOnWriteArrayList<>();
        Set<String> existing = new HashSet<>();
        ExtractionResultWriter writer = new ExtractionResultWriter() {
            @Override
            public boolean exists(ExtractionJob job, String relativePath) {
                return existing.contains(relativePath);
            }

            @Override
            public void write(ExtractionJob job, ExtractionResult result) {
                writtenResults.add(result);
            }

            @Override
            public void writeSummary(ExtractionJob job) {
            }
        };

        ManifestService manifestService = mock(ManifestService.class);
        when(manifestService.computeHashes(any(), any())).thenReturn(Map.of());
        JsRepositoryProcessingOrchestrator orchestrator =
                new JsRepositoryProcessingOrchestrator(scanner, selector, writer, new NonSubstantiveFileFilter(),
                        null, properties, manifestService);

        ExtractionJob job = new ExtractionJob(UUID.randomUUID(), repoRoot, repoRoot.resolve("out"), concurrencyLimit);

        orchestrator.run(job);

        assertThat(job.phase()).isEqualTo(JobPhase.COMPLETED);
        assertThat(job.totalCount()).isEqualTo(3);
        assertThat(job.succeededCount()).isEqualTo(2);
        assertThat(job.failedCount()).isEqualTo(1);
        assertThat(writtenResults).hasSize(2);
        assertThat(maxObservedConcurrency.get()).isLessThanOrEqualTo(concurrencyLimit);
    }

    @Test
    void skipsFilesWithExistingResultsWhenEnabled() throws IOException {
        write(repoRoot.resolve("a.js"), "const a = 1;");
        write(repoRoot.resolve("b.js"), "const b = 2;");

        ExtractionProperties properties = new ExtractionProperties(null, null, null, 300_000, 8, true, null);
        ChunkingProperties chunkingProperties = new ChunkingProperties(false, 0);
        RepositoryScannerService scanner = new RepositoryScannerService(properties, chunkingProperties, new LargeFileChunker(chunkingProperties));

        AtomicInteger callCount = new AtomicInteger();
        LogicExtractionAgent agent = new LogicExtractionAgent() {
            @Override
            public String name() {
                return "test-agent";
            }

            @Override
            public ExtractionResult extract(SourceFile file) {
                callCount.incrementAndGet();
                return ExtractionResult.success(file, name(), "{}", 1, null, null);
            }
        };

        AgentSelector selector = new AgentSelector(List.of(agent));
        ExtractionResultWriter writer = new ExtractionResultWriter() {
            @Override
            public boolean exists(ExtractionJob job, String relativePath) {
                return relativePath.equals("a.js");
            }

            @Override
            public void write(ExtractionJob job, ExtractionResult result) {
            }

            @Override
            public void writeSummary(ExtractionJob job) {
            }
        };

        ManifestService manifestService = mock(ManifestService.class);
        when(manifestService.computeHashes(any(), any())).thenReturn(Map.of());
        JsRepositoryProcessingOrchestrator orchestrator =
                new JsRepositoryProcessingOrchestrator(scanner, selector, writer, new NonSubstantiveFileFilter(),
                        null, properties, manifestService);
        ExtractionJob job = new ExtractionJob(UUID.randomUUID(), repoRoot, repoRoot.resolve("out"), 4);

        orchestrator.run(job);

        assertThat(callCount.get()).isEqualTo(1);
        assertThat(job.succeededCount()).isEqualTo(2);
    }

    @Test
    void delegatesToBatchExtractionServiceWhenExecutionModeIsBatch() throws IOException {
        write(repoRoot.resolve("a.js"), "const a = 1;");
        write(repoRoot.resolve("b.js"), "const b = 2;");

        ExtractionProperties properties = new ExtractionProperties(null, null, null, 300_000, 8, false, null);
        ChunkingProperties chunkingProperties = new ChunkingProperties(false, 0);
        RepositoryScannerService scanner = new RepositoryScannerService(properties, chunkingProperties, new LargeFileChunker(chunkingProperties));
        AgentSelector selector = new AgentSelector(List.of(unusedAgent()));
        BatchExtractionService batchExtractionService = mock(BatchExtractionService.class);

        ExtractionResultWriter writer = new ExtractionResultWriter() {
            @Override
            public boolean exists(ExtractionJob job, String relativePath) {
                return false;
            }

            @Override
            public void write(ExtractionJob job, ExtractionResult result) {
            }

            @Override
            public void writeSummary(ExtractionJob job) {
            }
        };

        ManifestService manifestService = mock(ManifestService.class);
        when(manifestService.computeHashes(any(), any())).thenReturn(Map.of());
        JsRepositoryProcessingOrchestrator orchestrator =
                new JsRepositoryProcessingOrchestrator(scanner, selector, writer, new NonSubstantiveFileFilter(),
                        batchExtractionService, properties, manifestService);

        ExtractionJob job = new ExtractionJob(UUID.randomUUID(), repoRoot, repoRoot.resolve("out"), 4,
                ExecutionMode.BATCH);

        orchestrator.run(job);

        verify(batchExtractionService).runBatch(eq(job), any());
        assertThat(job.phase()).isEqualTo(JobPhase.COMPLETED);
    }

    @Test
    void marksJobFailedWhenBatchExtractionServiceThrows() throws IOException {
        write(repoRoot.resolve("a.js"), "const a = 1;");

        ExtractionProperties properties = new ExtractionProperties(null, null, null, 300_000, 8, false, null);
        ChunkingProperties chunkingProperties = new ChunkingProperties(false, 0);
        RepositoryScannerService scanner = new RepositoryScannerService(properties, chunkingProperties, new LargeFileChunker(chunkingProperties));
        AgentSelector selector = new AgentSelector(List.of(unusedAgent()));
        BatchExtractionService batchExtractionService = mock(BatchExtractionService.class);
        doThrow(new RuntimeException("boom")).when(batchExtractionService).runBatch(any(), any());

        ExtractionResultWriter writer = new ExtractionResultWriter() {
            @Override
            public boolean exists(ExtractionJob job, String relativePath) {
                return false;
            }

            @Override
            public void write(ExtractionJob job, ExtractionResult result) {
            }

            @Override
            public void writeSummary(ExtractionJob job) {
            }
        };

        ManifestService manifestService = mock(ManifestService.class);
        when(manifestService.computeHashes(any(), any())).thenReturn(Map.of());
        JsRepositoryProcessingOrchestrator orchestrator =
                new JsRepositoryProcessingOrchestrator(scanner, selector, writer, new NonSubstantiveFileFilter(),
                        batchExtractionService, properties, manifestService);

        ExtractionJob job = new ExtractionJob(UUID.randomUUID(), repoRoot, repoRoot.resolve("out"), 4,
                ExecutionMode.BATCH);

        orchestrator.run(job);

        assertThat(job.phase()).isEqualTo(JobPhase.FAILED);
        assertThat(job.failureReason()).contains("boom");
    }

    @Test
    void processesASingleDroppedFileWhenJobRootIsAFileNotADirectory() throws IOException {
        Path file = repoRoot.resolve("dropped.js");
        write(file, "const a = 1;");

        ExtractionProperties properties = new ExtractionProperties(null, null, null, 300_000, 8, false, null);
        ChunkingProperties chunkingProperties = new ChunkingProperties(false, 0);
        RepositoryScannerService scanner = new RepositoryScannerService(properties, chunkingProperties, new LargeFileChunker(chunkingProperties));

        List<ExtractionResult> writtenResults = new CopyOnWriteArrayList<>();
        LogicExtractionAgent agent = new LogicExtractionAgent() {
            @Override
            public String name() {
                return "test-agent";
            }

            @Override
            public ExtractionResult extract(SourceFile sourceFile) {
                return ExtractionResult.success(sourceFile, name(), "{}", 1, null, null);
            }
        };
        AgentSelector selector = new AgentSelector(List.of(agent));
        ExtractionResultWriter writer = new ExtractionResultWriter() {
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

        ManifestService manifestService = mock(ManifestService.class);
        JsRepositoryProcessingOrchestrator orchestrator =
                new JsRepositoryProcessingOrchestrator(scanner, selector, writer, new NonSubstantiveFileFilter(),
                        null, properties, manifestService);
        ExtractionJob job = new ExtractionJob(UUID.randomUUID(), file, repoRoot.resolve("out"), 4);

        orchestrator.run(job);

        assertThat(job.phase()).isEqualTo(JobPhase.COMPLETED);
        assertThat(job.totalCount()).isEqualTo(1);
        assertThat(job.succeededCount()).isEqualTo(1);
        assertThat(writtenResults).extracting(ExtractionResult::relativePath).containsExactly("dropped.js");
    }

    @Test
    void incrementalJobOnlyProcessesChangedFiles() throws IOException {
        write(repoRoot.resolve("unchanged.js"), "const x = 1;");
        write(repoRoot.resolve("changed.js"), "const y = 2;");

        ExtractionProperties properties = new ExtractionProperties(null, null, null, 300_000, 8, false, null);
        ChunkingProperties chunkingProperties = new ChunkingProperties(false, 0);
        RepositoryScannerService scanner = new RepositoryScannerService(properties, chunkingProperties, new LargeFileChunker(chunkingProperties));

        List<String> extractedPaths = new CopyOnWriteArrayList<>();
        LogicExtractionAgent agent = new LogicExtractionAgent() {
            @Override
            public String name() { return "test-agent"; }

            @Override
            public ExtractionResult extract(SourceFile file) {
                extractedPaths.add(file.relativePath());
                return ExtractionResult.success(file, name(), "logic", 1, null, null);
            }
        };
        AgentSelector selector = new AgentSelector(List.of(agent));
        ExtractionResultWriter writer = new ExtractionResultWriter() {
            @Override
            public boolean exists(ExtractionJob job, String relativePath) { return false; }
            @Override
            public void write(ExtractionJob job, ExtractionResult result) {}
            @Override
            public void writeSummary(ExtractionJob job) {}
        };

        // Manifest says unchanged.js has its current hash, changed.js has a stale hash
        Map<String, String> previousHashes = Map.of(
                "unchanged.js", "correct-hash-matches-real-file",
                "changed.js", "stale-hash-does-not-match");

        ManifestService manifestService = mock(ManifestService.class);
        // Return current hashes that match unchanged.js but differ for changed.js
        when(manifestService.computeHashes(any(), any())).thenAnswer(inv -> {
            // Real hash for unchanged, different hash for changed → triggers diff
            return Map.of("unchanged.js", "correct-hash-matches-real-file",
                          "changed.js", "new-hash-after-edit");
        });
        when(manifestService.load(any())).thenReturn(
                Optional.of(new ManifestService.Manifest(repoRoot.resolve("out"), previousHashes)));
        when(manifestService.diff(any(), any())).thenReturn(
                new ManifestService.FileChanges(List.of(), List.of("changed.js"), List.of()));

        JsRepositoryProcessingOrchestrator orchestrator =
                new JsRepositoryProcessingOrchestrator(scanner, selector, writer, new NonSubstantiveFileFilter(),
                        null, properties, manifestService);

        ExtractionJob job = new ExtractionJob(UUID.randomUUID(), repoRoot, repoRoot.resolve("out"), 4,
                ExecutionMode.SYNC, true);

        orchestrator.run(job);

        assertThat(job.phase()).isEqualTo(JobPhase.COMPLETED);
        assertThat(job.totalCount()).isEqualTo(1); // only changed file counted
        assertThat(extractedPaths).containsExactly("changed.js");
    }

    private void write(Path path, String content) throws IOException {
        Files.writeString(path, content);
    }

    private LogicExtractionAgent unusedAgent() {
        return new LogicExtractionAgent() {
            @Override
            public String name() {
                return "unused-agent";
            }

            @Override
            public ExtractionResult extract(SourceFile file) {
                throw new UnsupportedOperationException("BATCH mode must not invoke the SYNC agent path");
            }
        };
    }
}
