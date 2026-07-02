package com.jslogicextractor.web;

import com.jslogicextractor.config.RuntimeSettings;
import com.jslogicextractor.orchestration.ExecutionMode;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

import java.util.HashMap;
import java.util.Map;
import java.util.Set;

/**
 * Mirrors StoryForge's GET/PUT /settings: reads/writes RuntimeSettings,
 * masking the Anthropic key the same way (last 4 chars only) and treating a
 * PUT that echoes back the current mask unchanged as "leave the secret
 * alone". PUT is admin-only, checked via the "role" request attribute
 * JwtAuthFilter sets from the shared JWT's claims.
 */
@RestController
@RequestMapping("/api/v1/settings")
public class SettingsController {

    private final RuntimeSettings runtimeSettings;

    public SettingsController(RuntimeSettings runtimeSettings) {
        this.runtimeSettings = runtimeSettings;
    }

    @GetMapping
    public SettingsResponse get() {
        return currentSettings();
    }

    @PutMapping
    public SettingsResponse update(@RequestBody SettingsUpdateRequest body, HttpServletRequest request) {
        requireAdmin(request);

        Map<String, String> changes = new HashMap<>();
        if (body.anthropicApiKey() != null && !body.anthropicApiKey().equals(maskSecret(runtimeSettings.anthropicApiKey()))) {
            changes.put("anthropicApiKey", body.anthropicApiKey());
        }
        if (body.anthropicModel() != null) {
            changes.put("anthropicModel", body.anthropicModel());
        }
        if (body.executionMode() != null) {
            try {
                ExecutionMode.valueOf(body.executionMode());
            } catch (IllegalArgumentException e) {
                throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "executionMode must be SYNC or BATCH");
            }
            changes.put("executionMode", body.executionMode());
        }
        if (body.qaModel() != null) {
            changes.put("qaModel", body.qaModel());
        }
        if (body.ollamaEnabled() != null) {
            changes.put("ollamaEnabled", String.valueOf(body.ollamaEnabled()));
        }
        if (body.ollamaBaseUrl() != null) {
            changes.put("ollamaBaseUrl", body.ollamaBaseUrl());
        }
        if (body.ollamaModel() != null) {
            changes.put("ollamaModel", body.ollamaModel());
        }

        if (!changes.isEmpty()) {
            runtimeSettings.update(changes);
        }
        return currentSettings();
    }

    private void requireAdmin(HttpServletRequest request) {
        Object role = request.getAttribute("role");
        if (!"admin".equals(role)) {
            throw new ResponseStatusException(HttpStatus.FORBIDDEN, "Admin privileges required");
        }
    }

    private SettingsResponse currentSettings() {
        return new SettingsResponse(
                runtimeSettings.anthropicModel(),
                maskSecret(runtimeSettings.anthropicApiKey()),
                runtimeSettings.executionMode().name(),
                runtimeSettings.qaModel(),
                runtimeSettings.ollamaEnabled(),
                runtimeSettings.ollamaBaseUrl(),
                runtimeSettings.ollamaModel(),
                RuntimeSettings.RESTART_REQUIRED_FIELDS);
    }

    private String maskSecret(String value) {
        if (value == null || value.isBlank()) {
            return "";
        }
        if (value.length() <= 4) {
            return "*".repeat(value.length());
        }
        return "…" + value.substring(value.length() - 4);
    }

    public record SettingsUpdateRequest(
            String anthropicApiKey,
            String anthropicModel,
            String executionMode,
            String qaModel,
            Boolean ollamaEnabled,
            String ollamaBaseUrl,
            String ollamaModel) {
    }

    public record SettingsResponse(
            String anthropicModel,
            String anthropicApiKeyMasked,
            String executionMode,
            String qaModel,
            boolean ollamaEnabled,
            String ollamaBaseUrl,
            String ollamaModel,
            Set<String> restartRequiredFields) {
    }
}
