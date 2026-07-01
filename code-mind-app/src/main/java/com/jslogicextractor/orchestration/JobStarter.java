package com.jslogicextractor.orchestration;

import com.jslogicextractor.incremental.ManifestService;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.web.server.ResponseStatusException;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Arrays;
import java.util.Optional;
import java.util.concurrent.ExecutorService;

/**
 * Validates a start-job request, registers it, and dispatches it off-thread. Shared by the REST
 * API and the Thymeleaf job-creation form so both go through identical validation/dispatch.
 *
 * <p>When no output directory is specified and a completed manifest already exists for the given
 * repository root, the job is automatically registered as an incremental run reusing the previous
 * output directory. This means only changed or new files are sent to Claude.
 */
@Service
public class JobStarter {

    private final JobRegistry jobRegistry;
    private final JsRepositoryProcessingOrchestrator orchestrator;
    private final ExecutorService extractionExecutor;
    private final ManifestService manifestService;

    public JobStarter(JobRegistry jobRegistry, JsRepositoryProcessingOrchestrator orchestrator,
                       ExecutorService extractionExecutor, ManifestService manifestService) {
        this.jobRegistry = jobRegistry;
        this.orchestrator = orchestrator;
        this.extractionExecutor = extractionExecutor;
        this.manifestService = manifestService;
    }

    /**
     * Starts a job scoped to exactly one file rather than a whole repository — used by the
     * input-directory watcher, where each dropped file becomes its own job.
     */
    public ExtractionJob startForFile(Path file) {
        Path resolved = file.toAbsolutePath().normalize();
        if (!Files.isRegularFile(resolved)) {
            throw new IllegalArgumentException("Not a file: " + resolved);
        }
        ExtractionJob job = jobRegistry.register(resolved, null, null, null);
        extractionExecutor.execute(() -> {
            try {
                orchestrator.run(job);
            } finally {
                jobRegistry.persist(job);
            }
        });
        return job;
    }

    public ExtractionJob start(String repositoryPath, String outputDirectory, Integer maxConcurrency,
                                String executionModeRaw) {
        Path repositoryRoot = Path.of(repositoryPath).toAbsolutePath().normalize();
        if (!Files.isDirectory(repositoryRoot)) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST,
                    "repositoryPath is not a directory: " + repositoryRoot);
        }
        ExecutionMode executionMode = parseExecutionMode(executionModeRaw);

        boolean incremental = false;
        Path resolvedOutputDirectory;
        if (outputDirectory != null && !outputDirectory.isBlank()) {
            // Explicit output directory → always a full run into that directory.
            resolvedOutputDirectory = Path.of(outputDirectory).toAbsolutePath().normalize();
        } else {
            // Auto-detect: if a manifest exists and its output directory is still on disk, run
            // incrementally reusing that directory; otherwise start a fresh full run.
            Optional<ManifestService.Manifest> manifest = manifestService.load(repositoryRoot);
            if (manifest.isPresent() && Files.isDirectory(manifest.get().outputDirectory())) {
                resolvedOutputDirectory = manifest.get().outputDirectory();
                incremental = true;
            } else {
                resolvedOutputDirectory = null;
            }
        }

        ExtractionJob job = jobRegistry.register(repositoryRoot, resolvedOutputDirectory, maxConcurrency,
                executionMode, incremental);
        extractionExecutor.execute(() -> {
            try {
                orchestrator.run(job);
            } finally {
                jobRegistry.persist(job);
            }
        });
        return job;
    }

    private ExecutionMode parseExecutionMode(String rawValue) {
        if (rawValue == null || rawValue.isBlank()) {
            return null;
        }
        try {
            return ExecutionMode.valueOf(rawValue.trim().toUpperCase());
        } catch (IllegalArgumentException e) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST,
                    "executionMode must be one of " + Arrays.toString(ExecutionMode.values()));
        }
    }
}
