package com.jslogicextractor.web;

import com.jslogicextractor.orchestration.ExtractionJob;
import com.jslogicextractor.orchestration.JobRegistry;
import com.jslogicextractor.orchestration.JobStarter;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.http.HttpStatus;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.web.server.ResponseStatusException;

import java.nio.file.Path;
import java.util.List;
import java.util.Optional;
import java.util.UUID;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.ArgumentMatchers.isNull;
import static org.mockito.BDDMockito.given;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.model;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.redirectedUrl;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.view;

@WebMvcTest(JobUiController.class)
class JobUiControllerTest {

    @Autowired
    private MockMvc mockMvc;

    @MockitoBean
    private JobRegistry jobRegistry;

    @MockitoBean
    private JobStarter jobStarter;

    @TempDir
    static Path repoRoot;

    @Test
    void jobsPageListsRegisteredJobs() throws Exception {
        ExtractionJob job = new ExtractionJob(UUID.randomUUID(), repoRoot, repoRoot.resolve("out"), 4);
        given(jobRegistry.findAll()).willReturn(List.of(job));

        mockMvc.perform(get("/ui/jobs"))
                .andExpect(status().isOk())
                .andExpect(view().name("jobs-list"))
                .andExpect(model().attributeExists("jobs"));
    }

    @Test
    void startJobRedirectsToProgressPage() throws Exception {
        ExtractionJob job = new ExtractionJob(UUID.randomUUID(), repoRoot, repoRoot.resolve("out"), 4);
        given(jobStarter.start(eq(repoRoot.toString()), isNull(), isNull(), isNull())).willReturn(job);

        mockMvc.perform(post("/ui/jobs").param("repositoryPath", repoRoot.toString()))
                .andExpect(status().is3xxRedirection())
                .andExpect(redirectedUrl("/ui/jobs/" + job.id()));
    }

    @Test
    void startJobRejectsBlankRepositoryPath() throws Exception {
        mockMvc.perform(post("/ui/jobs").param("repositoryPath", ""))
                .andExpect(status().isOk())
                .andExpect(view().name("jobs-list"))
                .andExpect(model().attributeExists("error"));
    }

    @Test
    void startJobShowsErrorWhenJobStarterRejects() throws Exception {
        given(jobStarter.start(any(), any(), any(), any()))
                .willThrow(new ResponseStatusException(HttpStatus.BAD_REQUEST, "repositoryPath is not a directory"));

        mockMvc.perform(post("/ui/jobs").param("repositoryPath", "/does/not/exist"))
                .andExpect(status().isOk())
                .andExpect(view().name("jobs-list"))
                .andExpect(model().attribute("error", "repositoryPath is not a directory"));
    }

    @Test
    void progressPageRendersKnownJob() throws Exception {
        ExtractionJob job = new ExtractionJob(UUID.randomUUID(), repoRoot, repoRoot.resolve("out"), 4);
        given(jobRegistry.find(job.id())).willReturn(Optional.of(job));

        mockMvc.perform(get("/ui/jobs/" + job.id()))
                .andExpect(status().isOk())
                .andExpect(view().name("job-progress"))
                .andExpect(model().attributeExists("job"));
    }

    @Test
    void progressPageReturnsNotFoundForUnknownJob() throws Exception {
        given(jobRegistry.find(any())).willReturn(Optional.empty());

        mockMvc.perform(get("/ui/jobs/" + UUID.randomUUID()))
                .andExpect(status().isNotFound());
    }

    @Test
    void askPageRendersKnownJob() throws Exception {
        ExtractionJob job = new ExtractionJob(UUID.randomUUID(), repoRoot, repoRoot.resolve("out"), 4);
        given(jobRegistry.find(job.id())).willReturn(Optional.of(job));

        mockMvc.perform(get("/ui/jobs/" + job.id() + "/ask"))
                .andExpect(status().isOk())
                .andExpect(view().name("job-ask"))
                .andExpect(model().attributeExists("job"));
    }

    @Test
    void askPageReturnsNotFoundForUnknownJob() throws Exception {
        given(jobRegistry.find(any())).willReturn(Optional.empty());

        mockMvc.perform(get("/ui/jobs/" + UUID.randomUUID() + "/ask"))
                .andExpect(status().isNotFound());
    }
}
