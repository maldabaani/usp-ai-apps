package com.jslogicextractor.web;

import com.jslogicextractor.monitoring.ErrorLogStore;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;
import java.util.Map;

/**
 * Mirrors StoryForge's GET /monitoring/errors: any authenticated request is
 * fine here (JwtAuthFilter already gated /api/* before this controller runs),
 * no admin check needed -- same as SettingsController's GET.
 */
@RestController
@RequestMapping("/api/v1/monitoring")
public class MonitoringController {

    @GetMapping("/errors")
    public Map<String, List<ErrorLogStore.ErrorRecord>> errors() {
        return Map.of("errors", ErrorLogStore.list());
    }
}
