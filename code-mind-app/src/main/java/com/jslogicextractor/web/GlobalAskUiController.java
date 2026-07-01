package com.jslogicextractor.web;

import com.jslogicextractor.orchestration.JobPhase;
import com.jslogicextractor.orchestration.JobRegistry;
import org.springframework.stereotype.Controller;
import org.springframework.ui.Model;
import org.springframework.web.bind.annotation.GetMapping;

import java.util.List;

@Controller
class GlobalAskUiController {

    private final JobRegistry jobRegistry;

    GlobalAskUiController(JobRegistry jobRegistry) {
        this.jobRegistry = jobRegistry;
    }

    @GetMapping("/ui/ask")
    public String globalAskPage(Model model) {
        List<JobResponse> completedJobs = jobRegistry.findAll().stream()
                .filter(j -> j.phase() == JobPhase.COMPLETED)
                .map(JobResponse::from)
                .toList();
        model.addAttribute("jobs", completedJobs);
        return "ask-global";
    }
}
