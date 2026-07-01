package com.jslogicextractor.web;

import com.jslogicextractor.orchestration.ExtractionJob;
import com.jslogicextractor.orchestration.JobRegistry;
import com.jslogicextractor.orchestration.JobStarter;
import com.jslogicextractor.output.OutputFileSnapshotService;
import com.jslogicextractor.qa.ExtractionQaService;
import com.jslogicextractor.qa.QaAnswer;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.http.MediaType;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;
import reactor.core.publisher.Flux;

import java.nio.file.Path;
import java.time.Instant;
import java.util.List;
import java.util.Optional;
import java.util.UUID;

import static org.hamcrest.Matchers.containsString;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.BDDMockito.given;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.asyncDispatch;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.request;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@WebMvcTest(ExtractionJobController.class)
class ExtractionJobControllerTest {

    @Autowired
    private MockMvc mockMvc;

    @MockitoBean
    private JobRegistry jobRegistry;

    @MockitoBean
    private JobStarter jobStarter;

    @MockitoBean
    private OutputFileSnapshotService outputFileSnapshotService;

    @MockitoBean
    private ExtractionQaService qaService;

    @TempDir
    static Path repoRoot;

    @Test
    void startJobReturnsAcceptedWithJobId() throws Exception {
        ExtractionJob job = new ExtractionJob(UUID.randomUUID(), repoRoot, repoRoot.resolve("out"), 4);
        given(jobStarter.start(any(), any(), any(), any())).willReturn(job);

        mockMvc.perform(post("/api/v1/extraction-jobs")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"repositoryPath\":" + quoted(repoRoot.toString()) + "}"))
                .andExpect(status().isAccepted())
                .andExpect(jsonPath("$.jobId").value(job.id().toString()));
    }

    @Test
    void getJobReturnsNotFoundForUnknownId() throws Exception {
        given(jobRegistry.find(any())).willReturn(Optional.empty());

        mockMvc.perform(get("/api/v1/extraction-jobs/" + UUID.randomUUID()))
                .andExpect(status().isNotFound());
    }

    @Test
    void listJobsReturnsAllRegisteredJobs() throws Exception {
        ExtractionJob job = new ExtractionJob(UUID.randomUUID(), repoRoot, repoRoot.resolve("out"), 4);
        given(jobRegistry.findAll()).willReturn(List.of(job));

        mockMvc.perform(get("/api/v1/extraction-jobs"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$[0].jobId").value(job.id().toString()));
    }

    @Test
    void listOutputFilesReturnsSnapshotForKnownJob() throws Exception {
        ExtractionJob job = new ExtractionJob(UUID.randomUUID(), repoRoot, repoRoot.resolve("out"), 4);
        given(jobRegistry.find(job.id())).willReturn(Optional.of(job));
        given(outputFileSnapshotService.recentFiles(any(), anyInt())).willReturn(
                List.of(new OutputFileSnapshotService.OutputFile("a.js.json", 42L, Instant.now())));

        mockMvc.perform(get("/api/v1/extraction-jobs/" + job.id() + "/output-files"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$[0].relativePath").value("a.js.json"));
    }

    @Test
    void listOutputFilesReturnsNotFoundForUnknownJob() throws Exception {
        given(jobRegistry.find(any())).willReturn(Optional.empty());

        mockMvc.perform(get("/api/v1/extraction-jobs/" + UUID.randomUUID() + "/output-files"))
                .andExpect(status().isNotFound());
    }

    @Test
    void askReturnsAnswerWithSourceFilesForKnownJob() throws Exception {
        ExtractionJob job = new ExtractionJob(UUID.randomUUID(), repoRoot, repoRoot.resolve("out"), 4);
        given(jobRegistry.find(job.id())).willReturn(Optional.of(job));
        given(qaService.ask(job, "what does auth.js do?"))
                .willReturn(new QaAnswer("It authenticates users.", List.of("auth.js")));

        mockMvc.perform(post("/api/v1/extraction-jobs/" + job.id() + "/qa")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"question\":\"what does auth.js do?\"}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.answer").value("It authenticates users."))
                .andExpect(jsonPath("$.sourceFiles[0]").value("auth.js"));
    }

    @Test
    void askReturnsNotFoundForUnknownJob() throws Exception {
        given(jobRegistry.find(any())).willReturn(Optional.empty());

        mockMvc.perform(post("/api/v1/extraction-jobs/" + UUID.randomUUID() + "/qa")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"question\":\"anything?\"}"))
                .andExpect(status().isNotFound());
    }

    @Test
    void askRejectsBlankQuestion() throws Exception {
        ExtractionJob job = new ExtractionJob(UUID.randomUUID(), repoRoot, repoRoot.resolve("out"), 4);
        given(jobRegistry.find(job.id())).willReturn(Optional.of(job));

        mockMvc.perform(post("/api/v1/extraction-jobs/" + job.id() + "/qa")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"question\":\"\"}"))
                .andExpect(status().isBadRequest());
    }

    @Test
    void askStreamReturnsServerSentEventsForKnownJob() throws Exception {
        ExtractionJob job = new ExtractionJob(UUID.randomUUID(), repoRoot, repoRoot.resolve("out"), 4);
        given(jobRegistry.find(job.id())).willReturn(Optional.of(job));
        given(qaService.askForStream(any(ExtractionJob.class), any(String.class))).willReturn(
                new ExtractionQaService.QaStreamResult(
                        List.of("auth.js"), Flux.just("It ", "authenticates ", "users.")));

        MvcResult mvcResult = mockMvc.perform(post("/api/v1/extraction-jobs/" + job.id() + "/qa/stream")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"question\":\"what does auth.js do?\"}"))
                .andExpect(request().asyncStarted())
                .andReturn();

        mvcResult.getAsyncResult(5000L);

        mockMvc.perform(asyncDispatch(mvcResult))
                .andExpect(status().isOk())
                .andExpect(content().contentTypeCompatibleWith(MediaType.TEXT_EVENT_STREAM))
                .andExpect(content().string(containsString("auth.js")))
                .andExpect(content().string(containsString("authenticates")));
    }

    @Test
    void askStreamReturnsNotFoundForUnknownJob() throws Exception {
        given(jobRegistry.find(any())).willReturn(Optional.empty());

        mockMvc.perform(post("/api/v1/extraction-jobs/" + UUID.randomUUID() + "/qa/stream")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"question\":\"anything?\"}"))
                .andExpect(status().isNotFound());
    }

    private String quoted(String value) {
        return "\"" + value.replace("\\", "\\\\") + "\"";
    }
}
