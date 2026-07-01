package com.jslogicextractor.orchestration;

import com.jslogicextractor.incremental.ManifestService;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.web.server.ResponseStatusException;

import java.nio.file.Path;
import java.util.Optional;
import java.util.concurrent.ExecutorService;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class JobStarterTest {

    @TempDir
    Path repoRoot;

    private final JobRegistry jobRegistry = mock(JobRegistry.class);
    private final JsRepositoryProcessingOrchestrator orchestrator = mock(JsRepositoryProcessingOrchestrator.class);
    private final ExecutorService extractionExecutor = mock(ExecutorService.class);
    private final ManifestService manifestService = mock(ManifestService.class);
    private final JobStarter jobStarter = new JobStarter(jobRegistry, orchestrator, extractionExecutor, manifestService);

    @Test
    void startsJobAndDispatchesOffThread() {
        ExtractionJob job = new ExtractionJob(java.util.UUID.randomUUID(), repoRoot, repoRoot.resolve("out"), 4);
        when(manifestService.load(any())).thenReturn(Optional.empty());
        when(jobRegistry.register(any(), any(), any(), any(), any(Boolean.class))).thenReturn(job);

        ExtractionJob result = jobStarter.start(repoRoot.toString(), null, null, null);

        assertThat(result).isEqualTo(job);
        verify(extractionExecutor).execute(any());
    }

    @Test
    void rejectsNonDirectoryPath() {
        Path missing = repoRoot.resolve("does-not-exist");

        assertThatThrownBy(() -> jobStarter.start(missing.toString(), null, null, null))
                .isInstanceOf(ResponseStatusException.class)
                .hasMessageContaining("not a directory");
    }

    @Test
    void rejectsInvalidExecutionMode() {
        assertThatThrownBy(() -> jobStarter.start(repoRoot.toString(), null, null, "BOGUS"))
                .isInstanceOf(ResponseStatusException.class)
                .hasMessageContaining("executionMode");
    }

    @Test
    void startsJobForFileAndDispatchesOffThread() throws java.io.IOException {
        Path file = repoRoot.resolve("dropped.js");
        java.nio.file.Files.writeString(file, "const x = 1;");
        ExtractionJob job = new ExtractionJob(java.util.UUID.randomUUID(), file, repoRoot.resolve("out"), 4);
        when(jobRegistry.register(any(), any(), any(), any())).thenReturn(job);

        ExtractionJob result = jobStarter.startForFile(file);

        assertThat(result).isEqualTo(job);
        verify(extractionExecutor).execute(any());
    }

    @Test
    void rejectsNonFilePathForStartForFile() {
        assertThatThrownBy(() -> jobStarter.startForFile(repoRoot))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("Not a file");
    }
}
