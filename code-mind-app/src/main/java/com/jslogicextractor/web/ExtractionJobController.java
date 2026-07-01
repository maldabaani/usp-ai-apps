package com.jslogicextractor.web;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.jslogicextractor.agent.ExtractionResult;
import com.jslogicextractor.orchestration.ExtractionJob;
import com.jslogicextractor.orchestration.JobRegistry;
import com.jslogicextractor.orchestration.JobStarter;
import com.jslogicextractor.output.OutputFileSnapshotService;
import com.jslogicextractor.qa.ExtractionQaService;
import com.jslogicextractor.qa.QaAnswer;
import jakarta.validation.Valid;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.server.ResponseStatusException;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.io.IOException;
import java.time.Instant;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;

@RestController
@RequestMapping("/api/v1/extraction-jobs")
public class ExtractionJobController {

    private static final int OUTPUT_FILES_LIMIT = 50;

    private final JobRegistry jobRegistry;
    private final JobStarter jobStarter;
    private final OutputFileSnapshotService outputFileSnapshotService;
    private final ExtractionQaService qaService;
    private final ObjectMapper objectMapper;

    public ExtractionJobController(JobRegistry jobRegistry,
                                    JobStarter jobStarter,
                                    OutputFileSnapshotService outputFileSnapshotService,
                                    ExtractionQaService qaService,
                                    ObjectMapper objectMapper) {
        this.jobRegistry = jobRegistry;
        this.jobStarter = jobStarter;
        this.outputFileSnapshotService = outputFileSnapshotService;
        this.qaService = qaService;
        this.objectMapper = objectMapper;
    }

    @PostMapping
    public ResponseEntity<JobResponse> startJob(@Valid @RequestBody StartJobRequest request) {
        ExtractionJob job = jobStarter.start(request.repositoryPath(), request.outputDirectory(),
                request.maxConcurrency(), request.executionMode());
        return ResponseEntity.accepted().body(JobResponse.from(job));
    }

    @GetMapping("/{jobId}")
    public ResponseEntity<JobResponse> getJob(@PathVariable UUID jobId) {
        return jobRegistry.find(jobId)
                .map(job -> ResponseEntity.ok(JobResponse.from(job)))
                .orElseGet(() -> ResponseEntity.notFound().build());
    }

    @PostMapping("/{jobId}/cancel")
    public ResponseEntity<Void> cancelJob(@PathVariable UUID jobId) {
        requireJob(jobId).requestCancel();
        return ResponseEntity.noContent().build();
    }

    @DeleteMapping("/{jobId}")
    public ResponseEntity<Void> deleteJob(@PathVariable UUID jobId) {
        requireJob(jobId);
        jobRegistry.delete(jobId);
        return ResponseEntity.noContent().build();
    }

    @DeleteMapping
    public ResponseEntity<Void> clearAll() {
        jobRegistry.clearAll();
        return ResponseEntity.noContent().build();
    }

    @GetMapping(value = "/{jobId}/export", produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<byte[]> exportJob(@PathVariable UUID jobId) {
        ExtractionJob job = requireJob(jobId);
        try {
            byte[] json = buildExportJson(job);
            String filename = "codemind-" + jobId.toString().replace("-", "").substring(0, 8) + ".json";
            return ResponseEntity.ok()
                    .header("Content-Disposition", "attachment; filename=\"" + filename + "\"")
                    .contentType(MediaType.APPLICATION_JSON)
                    .body(json);
        } catch (IOException e) {
            throw new ResponseStatusException(HttpStatus.INTERNAL_SERVER_ERROR, "Export failed: " + e.getMessage());
        }
    }

    private byte[] buildExportJson(ExtractionJob job) throws IOException {
        List<Object> files = new ArrayList<>();
        for (OutputFileSnapshotService.OutputFile of : outputFileSnapshotService.recentFiles(job, Integer.MAX_VALUE)) {
            Optional<String> raw = outputFileSnapshotService.readOutputFile(job, of.relativePath());
            if (raw.isEmpty()) continue;
            ExtractionResult result = objectMapper.readValue(raw.get(), ExtractionResult.class);
            if (!result.success() || result.skipped() || result.content() == null) continue;
            try {
                files.add(objectMapper.readValue(result.content(), Object.class));
            } catch (Exception ignored) {}
        }
        Map<String, Object> export = new LinkedHashMap<>();
        export.put("jobId", job.id().toString());
        export.put("repositoryRoot", job.repositoryRoot().toString());
        export.put("exportedAt", Instant.now().toString());
        export.put("totalExtracted", files.size());
        export.put("files", files);
        return objectMapper.writerWithDefaultPrettyPrinter().writeValueAsBytes(export);
    }

    @GetMapping
    public ResponseEntity<List<JobResponse>> listJobs() {
        List<JobResponse> jobs = jobRegistry.findAll().stream()
                .map(JobResponse::from)
                .toList();
        return ResponseEntity.ok(jobs);
    }

    @GetMapping("/{jobId}/output-files")
    public ResponseEntity<List<OutputFileResponse>> listOutputFiles(@PathVariable UUID jobId) {
        ExtractionJob job = requireJob(jobId);
        List<OutputFileResponse> files = outputFileSnapshotService.recentFiles(job, OUTPUT_FILES_LIMIT).stream()
                .map(OutputFileResponse::from)
                .toList();
        return ResponseEntity.ok(files);
    }

    @GetMapping(value = "/{jobId}/output-file", produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<String> readOutputFile(@PathVariable UUID jobId,
                                                 @RequestParam String relativePath) {
        ExtractionJob job = requireJob(jobId);
        return outputFileSnapshotService.readOutputFile(job, relativePath)
                .map(content -> ResponseEntity.ok().contentType(MediaType.APPLICATION_JSON).body(content))
                .orElseGet(() -> ResponseEntity.notFound().build());
    }

    @GetMapping("/{jobId}/failed-files")
    public ResponseEntity<List<FailedFileResponse>> listFailedFiles(@PathVariable UUID jobId) {
        ExtractionJob job = requireJob(jobId);
        List<FailedFileResponse> failed = outputFileSnapshotService.listFailedFiles(job).stream()
                .map(FailedFileResponse::from)
                .toList();
        return ResponseEntity.ok(failed);
    }

    @PostMapping("/{jobId}/qa")
    public ResponseEntity<QaResponse> ask(@PathVariable UUID jobId, @Valid @RequestBody QaRequest request) {
        ExtractionJob job = requireJob(jobId);
        QaAnswer answer = qaService.ask(job, request.question());
        return ResponseEntity.ok(QaResponse.from(answer));
    }

    @PostMapping("/{jobId}/qa/stream")
    public SseEmitter askStream(@PathVariable UUID jobId, @Valid @RequestBody QaRequest request) {
        ExtractionJob job = requireJob(jobId);
        SseEmitter emitter = new SseEmitter(120_000L);
        new Thread(() -> {
            try {
                ExtractionQaService.QaStreamResult stream = qaService.askForStream(job, request.question());
                emitter.send(SseEmitter.event()
                        .name("sources")
                        .data(objectMapper.writeValueAsString(stream.sourceFiles())));
                stream.textFlux()
                        .doOnNext(chunk -> {
                            try {
                                emitter.send(SseEmitter.event().name("chunk").data(objectMapper.writeValueAsString(chunk)));
                            } catch (IOException ex) {
                                throw new RuntimeException(ex);
                            }
                        })
                        .blockLast();
                emitter.complete();
            } catch (Exception e) {
                emitter.completeWithError(e);
            }
        }, "qa-sse-stream").start();
        return emitter;
    }

    private ExtractionJob requireJob(UUID jobId) {
        return jobRegistry.find(jobId)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "No such job: " + jobId));
    }
}
