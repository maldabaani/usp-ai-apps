package com.jslogicextractor.agent;

import com.jslogicextractor.config.RuntimeSettings;
import com.jslogicextractor.orchestration.ExecutionMode;
import com.jslogicextractor.prompt.LogicExtractionPromptTemplates;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.ai.chat.client.ChatClient;
import org.springframework.retry.support.RetryTemplate;

import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.nio.file.Path;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;

/**
 * Verifies the settings-screen hot-reload path (RuntimeSettings' generation
 * counter -&gt; ClaudeLogicExtractionAgent rebuilding its ChatClient) without
 * making a real network call to Anthropic -- extract() itself would try a
 * genuine HTTP request with whatever key is configured, so this drives the
 * private rebuildIfNeeded() method directly via reflection instead, matching
 * this suite's "no external processes" convention.
 */
class ClaudeLogicExtractionAgentTest {

    @TempDir
    Path tempDir;

    @Test
    void rebuildsChatClientOnlyWhenSettingsGenerationChanges() throws Exception {
        RuntimeSettings settings = newRuntimeSettings("key-one", "model-one");
        ClaudeLogicExtractionAgent agent = new ClaudeLogicExtractionAgent(
                settings, mock(RetryTemplate.class), mock(LogicExtractionPromptTemplates.class));

        ChatClient firstClient = chatClientField(agent);
        int firstGeneration = builtAtGenerationField(agent);
        assertThat(firstClient).isNotNull();
        assertThat(firstGeneration).isEqualTo(settings.generation());

        // Calling rebuildIfNeeded() again with nothing changed must not rebuild.
        invokeRebuildIfNeeded(agent);
        assertThat(chatClientField(agent)).isSameAs(firstClient);

        // A settings-screen change (bumps RuntimeSettings' generation) must trigger a rebuild
        // on the next call -- this is exactly what happens on the next extract() after a
        // PUT /api/v1/settings changes the Anthropic key/model, with no restart involved.
        settings.update(Map.of("anthropicApiKey", "key-two", "anthropicModel", "model-two"));
        invokeRebuildIfNeeded(agent);

        ChatClient secondClient = chatClientField(agent);
        assertThat(secondClient).isNotSameAs(firstClient);
        assertThat(builtAtGenerationField(agent)).isEqualTo(settings.generation());
    }

    private RuntimeSettings newRuntimeSettings(String apiKey, String model) {
        return new RuntimeSettings(
                apiKey,
                model,
                tempDir.resolve("codemind-runtime.properties").toString(),
                new com.jslogicextractor.config.ExtractionProperties(
                        null, null, null, 0, 0, true, ExecutionMode.SYNC),
                new com.jslogicextractor.config.QaProperties(null, null, null, 0),
                new com.jslogicextractor.config.OllamaProperties(false, null, null, 0, 0.0, 0));
    }

    private ChatClient chatClientField(ClaudeLogicExtractionAgent agent) throws Exception {
        Field field = ClaudeLogicExtractionAgent.class.getDeclaredField("chatClient");
        field.setAccessible(true);
        return (ChatClient) field.get(agent);
    }

    private int builtAtGenerationField(ClaudeLogicExtractionAgent agent) throws Exception {
        Field field = ClaudeLogicExtractionAgent.class.getDeclaredField("builtAtGeneration");
        field.setAccessible(true);
        return (int) field.get(agent);
    }

    private void invokeRebuildIfNeeded(ClaudeLogicExtractionAgent agent) throws Exception {
        Method method = ClaudeLogicExtractionAgent.class.getDeclaredMethod("rebuildIfNeeded");
        method.setAccessible(true);
        method.invoke(agent);
    }
}
