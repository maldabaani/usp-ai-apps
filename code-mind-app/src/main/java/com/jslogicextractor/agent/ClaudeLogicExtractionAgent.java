package com.jslogicextractor.agent;

import com.jslogicextractor.prompt.LogicExtractionPromptTemplates;
import com.jslogicextractor.scanner.SourceFile;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.ai.chat.client.ChatClient;
import org.springframework.ai.chat.metadata.Usage;
import org.springframework.ai.chat.model.ChatResponse;
import org.springframework.ai.chat.prompt.Prompt;
import org.springframework.boot.autoconfigure.condition.ConditionalOnExpression;
import org.springframework.stereotype.Component;

@Component
@ConditionalOnExpression("T(org.springframework.util.StringUtils).hasText('${spring.ai.anthropic.api-key:}')")
public class ClaudeLogicExtractionAgent implements LogicExtractionAgent {

    private static final Logger log = LoggerFactory.getLogger(ClaudeLogicExtractionAgent.class);
    private static final String NAME = "claude-logic-extractor";

    private final ChatClient chatClient;
    private final LogicExtractionPromptTemplates promptTemplates;

    public ClaudeLogicExtractionAgent(ChatClient.Builder chatClientBuilder,
                                       LogicExtractionPromptTemplates promptTemplates) {
        this.chatClient = chatClientBuilder.build();
        this.promptTemplates = promptTemplates;
    }

    @Override
    public String name() {
        return NAME;
    }

    @Override
    public ExtractionResult extract(SourceFile file) {
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
}
