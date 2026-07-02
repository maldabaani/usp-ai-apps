package com.jslogicextractor.config;

import com.jslogicextractor.orchestration.ExecutionMode;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Map;
import java.util.Properties;
import java.util.Set;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;

/**
 * Mutable, live-updatable mirror of the settings the /settings screen can
 * change, backed by a small on-disk properties file (codemind-runtime.properties
 * by default) that survives a restart -- the equivalent of StoryForge's
 * backend/.env + config_store.py, adapted to Spring's config model, which has
 * no direct analogue since @ConfigurationProperties records are bound once at
 * startup and are otherwise immutable.
 *
 * <p>Only anthropicApiKey/anthropicModel/executionMode actually take effect
 * without a restart today (see ClaudeLogicExtractionAgent's rebuild-on-change
 * and JobRegistry's per-job default) -- qaModel and every Ollama field are
 * still persisted and shown in the settings screen, but require a restart to
 * apply: ollamaEnabled gates a Spring bean's very existence via
 * {@code @ConditionalOnProperty}, which Spring only evaluates once at startup,
 * and neither ExtractionQaService nor OllamaLogicExtractionAgent are wired to
 * re-resolve their ChatClient on a settings change (unlike Claude's agent).
 * RESTART_REQUIRED_FIELDS below is the source of truth the settings API uses
 * to tell the UI which is which.
 */
@Component
public class RuntimeSettings {

    private static final Logger log = LoggerFactory.getLogger(RuntimeSettings.class);

    public static final Set<String> RESTART_REQUIRED_FIELDS =
            Set.of("qaModel", "ollamaEnabled", "ollamaBaseUrl", "ollamaModel");

    private final Path settingsFilePath;

    private final AtomicReference<String> anthropicApiKey;
    private final AtomicReference<String> anthropicModel;
    private final AtomicReference<ExecutionMode> executionMode;
    private final AtomicReference<String> qaModel;
    private final AtomicReference<Boolean> ollamaEnabled;
    private final AtomicReference<String> ollamaBaseUrl;
    private final AtomicReference<String> ollamaModel;
    private final AtomicInteger generation = new AtomicInteger();

    public RuntimeSettings(
            @Value("${spring.ai.anthropic.api-key:}") String initialAnthropicApiKey,
            @Value("${spring.ai.anthropic.chat.options.model:claude-sonnet-4-5-20250929}") String initialAnthropicModel,
            @Value("${codemind.runtime-settings-path:./codemind-runtime.properties}") String settingsPath,
            ExtractionProperties extractionProperties,
            QaProperties qaProperties,
            OllamaProperties ollamaProperties) {
        this.settingsFilePath = Path.of(settingsPath);
        Properties overrides = loadOverrides();

        this.anthropicApiKey = new AtomicReference<>(overrides.getProperty("anthropicApiKey", initialAnthropicApiKey));
        this.anthropicModel = new AtomicReference<>(overrides.getProperty("anthropicModel", initialAnthropicModel));
        this.executionMode = new AtomicReference<>(ExecutionMode.valueOf(
                overrides.getProperty("executionMode", extractionProperties.executionMode().name())));
        this.qaModel = new AtomicReference<>(overrides.getProperty("qaModel", qaProperties.model()));
        this.ollamaEnabled = new AtomicReference<>(Boolean.parseBoolean(
                overrides.getProperty("ollamaEnabled", String.valueOf(ollamaProperties.enabled()))));
        this.ollamaBaseUrl = new AtomicReference<>(overrides.getProperty("ollamaBaseUrl", ollamaProperties.baseUrl()));
        this.ollamaModel = new AtomicReference<>(overrides.getProperty("ollamaModel", ollamaProperties.model()));
    }

    private Properties loadOverrides() {
        Properties properties = new Properties();
        if (Files.isRegularFile(settingsFilePath)) {
            try (var in = Files.newInputStream(settingsFilePath)) {
                properties.load(in);
            } catch (IOException e) {
                log.warn("Failed to read {}: {}", settingsFilePath, e.getMessage());
            }
        }
        return properties;
    }

    public String anthropicApiKey() {
        return anthropicApiKey.get();
    }

    public String anthropicModel() {
        return anthropicModel.get();
    }

    public ExecutionMode executionMode() {
        return executionMode.get();
    }

    public String qaModel() {
        return qaModel.get();
    }

    public boolean ollamaEnabled() {
        return ollamaEnabled.get();
    }

    public String ollamaBaseUrl() {
        return ollamaBaseUrl.get();
    }

    public String ollamaModel() {
        return ollamaModel.get();
    }

    public int generation() {
        return generation.get();
    }

    /**
     * Applies and persists a partial set of changes (keys matching the field
     * names above; unrecognized keys are ignored). Persists to disk first so
     * a crash between the two steps never leaves the in-memory value out of
     * sync with what's saved.
     */
    public synchronized void update(Map<String, String> changes) {
        Properties merged = loadOverrides();
        changes.forEach(merged::setProperty);
        try {
            Path parent = settingsFilePath.toAbsolutePath().getParent();
            if (parent != null) {
                Files.createDirectories(parent);
            }
            try (var out = Files.newOutputStream(settingsFilePath)) {
                merged.store(out, "CodeMind runtime settings -- edited via the /settings screen");
            }
        } catch (IOException e) {
            log.error("Failed to persist runtime settings to {}: {}", settingsFilePath, e.getMessage());
        }

        if (changes.containsKey("anthropicApiKey")) anthropicApiKey.set(changes.get("anthropicApiKey"));
        if (changes.containsKey("anthropicModel")) anthropicModel.set(changes.get("anthropicModel"));
        if (changes.containsKey("executionMode")) executionMode.set(ExecutionMode.valueOf(changes.get("executionMode")));
        if (changes.containsKey("qaModel")) qaModel.set(changes.get("qaModel"));
        if (changes.containsKey("ollamaEnabled")) ollamaEnabled.set(Boolean.parseBoolean(changes.get("ollamaEnabled")));
        if (changes.containsKey("ollamaBaseUrl")) ollamaBaseUrl.set(changes.get("ollamaBaseUrl"));
        if (changes.containsKey("ollamaModel")) ollamaModel.set(changes.get("ollamaModel"));
        generation.incrementAndGet();
    }
}
