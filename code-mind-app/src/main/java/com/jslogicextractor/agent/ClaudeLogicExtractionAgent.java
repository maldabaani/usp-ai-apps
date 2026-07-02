package com.jslogicextractor.agent;

import com.jslogicextractor.config.RuntimeSettings;
import com.jslogicextractor.prompt.LogicExtractionPromptTemplates;
import com.jslogicextractor.scanner.SourceFile;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.ai.anthropic.AnthropicChatModel;
import org.springframework.ai.anthropic.AnthropicChatOptions;
import org.springframework.ai.anthropic.api.AnthropicApi;
import org.springframework.ai.chat.client.ChatClient;
import org.springframework.ai.chat.metadata.Usage;
import org.springframework.ai.chat.model.ChatResponse;
import org.springframework.ai.chat.prompt.Prompt;
import org.springframework.boot.autoconfigure.condition.ConditionalOnExpression;
import org.springframework.retry.support.RetryTemplate;
import org.springframework.stereotype.Component;

@Component
@ConditionalOnExpression("T(org.springframework.util.StringUtils).hasText('${spring.ai.anthropic.api-key:}')")
public class ClaudeLogicExtractionAgent implements LogicExtractionAgent {

    private static final Logger log = LoggerFactory.getLogger(ClaudeLogicExtractionAgent.class);
    private static final String NAME = "claude-logic-extractor";

    private final RuntimeSettings runtimeSettings;
    private final RetryTemplate retryTemplate;
    private final LogicExtractionPromptTemplates promptTemplates;

    private volatile ChatClient chatClient;
    private volatile int builtAtGeneration = -1;

    /**
     * Builds its own ChatClient from RuntimeSettings (instead of the
     * Spring AI-autoconfigured ChatClient.Builder the original code used)
     * so a settings-screen change to the Anthropic key/model actually takes
     * effect without a restart -- the autoconfigured builder wraps a
     * ChatModel bean constructed once at startup from application.yml and
     * never rebuilt. retryTemplate is still the autoconfigured bean (built
     * from spring.ai.retry.* in application.yml), reused as-is since retry
     * policy isn't one of the hot-reloadable fields.
     */
    public ClaudeLogicExtractionAgent(RuntimeSettings runtimeSettings,
                                       RetryTemplate retryTemplate,
                                       LogicExtractionPromptTemplates promptTemplates) {
        this.runtimeSettings = runtimeSettings;
        this.retryTemplate = retryTemplate;
        this.promptTemplates = promptTemplates;
        rebuildIfNeeded();
    }

    @Override
    public String name() {
        return NAME;
    }

    @Override
    public ExtractionResult extract(SourceFile file) {
        rebuildIfNeeded();
        long start = System.currentTimeMillis();
        try {
            Prompt prompt = promptTemplates.buildExtractionPrompt(file);
            ChatResponse response = chatClient.prompt(prompt).call().chatResponse();
            String text = response.getResult().getOutput().getText();
            Usage usage = response.getMetadata().getUsage();
            Integer promptTokens = usage != null ? usage.getPromptTokens() : null;
            Integer completionTokens = usage != null ? usage.getCompletionTokens() : null;
            return ExtractionResult.success(file, NAME, text, System.currentTimeMillis() - start,
                    promptTokens, completionTokens);
        } catch (Exception ex) {
            log.warn("Extraction failed for {}: {}", file.relativePath(), ex.getMessage());
            return ExtractionResult.failure(file, NAME, ex.getMessage(), System.currentTimeMillis() - start);
        }
    }

    /**
     * Rebuilds the ChatClient only when RuntimeSettings has actually changed
     * since the last build (tracked via its generation counter), not on
     * every single file extraction. Synchronized since multiple extraction
     * threads call extract() concurrently (see
     * JsRepositoryProcessingOrchestrator's per-file thread pool).
     */
    private synchronized void rebuildIfNeeded() {
        int currentGeneration = runtimeSettings.generation();
        if (chatClient != null && builtAtGeneration == currentGeneration) {
            return;
        }
        AnthropicApi anthropicApi = AnthropicApi.builder()
                .apiKey(runtimeSettings.anthropicApiKey())
                .build();
        AnthropicChatOptions options = AnthropicChatOptions.builder()
                .model(runtimeSettings.anthropicModel())
                .build();
        AnthropicChatModel chatModel = AnthropicChatModel.builder()
                .anthropicApi(anthropicApi)
                .defaultOptions(options)
                .retryTemplate(retryTemplate)
                .build();
        this.chatClient = ChatClient.builder(chatModel).build();
        this.builtAtGeneration = currentGeneration;
    }
}
