package com.jslogicextractor.web;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.jslogicextractor.orchestration.ExtractionJob;
import com.jslogicextractor.orchestration.JobPhase;
import com.jslogicextractor.orchestration.JobRegistry;
import com.jslogicextractor.qa.ExtractionQaService;
import jakarta.validation.Valid;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.io.IOException;
import java.util.List;

@RestController
@RequestMapping("/api/v1/ask")
public class GlobalAskController {

    private final JobRegistry jobRegistry;
    private final ExtractionQaService qaService;
    private final ObjectMapper objectMapper;

    public GlobalAskController(JobRegistry jobRegistry, ExtractionQaService qaService,
                                ObjectMapper objectMapper) {
        this.jobRegistry = jobRegistry;
        this.qaService = qaService;
        this.objectMapper = objectMapper;
    }

    @PostMapping("/stream")
    public SseEmitter askAllStream(@Valid @RequestBody QaRequest request) {
        List<ExtractionJob> jobs = jobRegistry.findAll().stream()
                .filter(j -> j.phase() == JobPhase.COMPLETED)
                .toList();
        SseEmitter emitter = new SseEmitter(120_000L);
        new Thread(() -> {
            try {
                ExtractionQaService.QaStreamResult stream = qaService.askForStream(jobs, request.question());
                emitter.send(SseEmitter.event()
                        .name("sources")
                        .data(objectMapper.writeValueAsString(stream.sourceFiles())));
                stream.textFlux()
                        .doOnNext(chunk -> {
                            try {
                                emitter.send(SseEmitter.event().name("chunk")
                                        .data(objectMapper.writeValueAsString(chunk)));
                            } catch (IOException ex) {
                                throw new RuntimeException(ex);
                            }
                        })
                        .blockLast();
                emitter.complete();
            } catch (Exception e) {
                emitter.completeWithError(e);
            }
        }, "global-qa-sse-stream").start();
        return emitter;
    }
}
