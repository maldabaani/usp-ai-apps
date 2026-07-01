package com.jslogicextractor.web;

import com.jslogicextractor.orchestration.ExtractionJob;
import com.jslogicextractor.orchestration.JobRegistry;
import com.jslogicextractor.orchestration.JobStarter;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Controller;
import org.springframework.ui.Model;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.server.ResponseStatusException;

import java.util.Comparator;
import java.util.List;
import java.util.UUID;

/**
 * Thymeleaf UI: a jobs list/start-form page, a per-job progress page, and a per-job Q&A page.
 * Live data on the progress/ask pages comes from the existing JSON API via client-side polling/fetch
 * rather than server-side push, so these handlers only need to render the initial page shell.
 */
@Controller
@RequestMapping("/ui/jobs")
class JobUiController {

    private final JobRegistry jobRegistry;
    private final JobStarter jobStarter;

    JobUiController(JobRegistry jobRegistry, JobStarter jobStarter) {
        this.jobRegistry = jobRegistry;
        this.jobStarter = jobStarter;
    }

    @GetMapping
    public String jobsPage(Model model) {
        List<JobResponse> jobs = jobRegistry.findAll().stream().map(JobResponse::from).toList();
        model.addAttribute("jobs", jobs);
        model.addAttribute("recentJobId", recentJobId(jobs));
        return "jobs-list";
    }

    @PostMapping
    public String startJob(@RequestParam String repositoryPath,
                            @RequestParam(required = false) String outputDirectory,
                            @RequestParam(required = false) String maxConcurrency,
                            @RequestParam(required = false) String executionMode,
                            Model model) {
        if (repositoryPath == null || repositoryPath.isBlank()) {
            return showJobsPageWithError(model, "Repository path is required.");
        }
        try {
            Integer parsedConcurrency = (maxConcurrency == null || maxConcurrency.isBlank())
                    ? null
                    : Integer.parseInt(maxConcurrency.trim());
            ExtractionJob job = jobStarter.start(repositoryPath, outputDirectory, parsedConcurrency, executionMode);
            return "redirect:/ui/jobs/" + job.id();
        } catch (NumberFormatException e) {
            return showJobsPageWithError(model, "Max concurrency must be a whole number.");
        } catch (ResponseStatusException e) {
            return showJobsPageWithError(model, e.getReason());
        }
    }

    @GetMapping("/{jobId}")
    public String progressPage(@PathVariable UUID jobId, Model model) {
        model.addAttribute("job", JobResponse.from(requireJob(jobId)));
        return "job-progress";
    }

    @GetMapping("/{jobId}/ask")
    public String askPage(@PathVariable UUID jobId, Model model) {
        model.addAttribute("job", JobResponse.from(requireJob(jobId)));
        return "job-ask";
    }

    private String showJobsPageWithError(Model model, String error) {
        model.addAttribute("error", error);
        List<JobResponse> jobs = jobRegistry.findAll().stream().map(JobResponse::from).toList();
        model.addAttribute("jobs", jobs);
        model.addAttribute("recentJobId", recentJobId(jobs));
        return "jobs-list";
    }

    private static String recentJobId(List<JobResponse> jobs) {
        return jobs.stream()
                .max(Comparator.comparing(JobResponse::createdAt))
                .map(JobResponse::jobId)
                .orElse(null);
    }

    private ExtractionJob requireJob(UUID jobId) {
        return jobRegistry.find(jobId)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "No such job: " + jobId));
    }
}
