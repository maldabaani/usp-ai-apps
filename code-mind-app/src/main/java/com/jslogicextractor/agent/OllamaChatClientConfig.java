package com.jslogicextractor.agent;

import com.jslogicextractor.config.OllamaProperties;
import org.springframework.ai.chat.client.ChatClient;
import org.springframework.ai.ollama.OllamaChatModel;
import org.springframework.ai.ollama.api.OllamaApi;
import org.springframework.ai.ollama.api.OllamaChatOptions;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/**
 * The OllamaApi/OllamaChatModel here are plain Java objects, not Spring beans: the app already
 * pins spring.ai.model.chat to anthropic, and registering another ChatModel bean would make the
 * autoconfigured ChatClient.Builder's ChatModel dependency ambiguous. Only the resulting
 * ChatClient is exposed as a bean, under its own name, leaving the Claude agent's wiring untouched.
 */
@Configuration
@ConditionalOnProperty(prefix = "jsprocessor.ollama", name = "enabled", havingValue = "true")
public class OllamaChatClientConfig {

    @Bean
    public ChatClient ollamaChatClient(OllamaProperties properties) {
        OllamaApi ollamaApi = OllamaApi.builder()
                .baseUrl(properties.baseUrl())
                .build();
        OllamaChatOptions options = OllamaChatOptions.builder()
                .model(properties.model())
                .temperature(properties.temperature())
                .numPredict(properties.maxTokens())
                .numCtx(properties.numCtx())
                .build();
        OllamaChatModel chatModel = OllamaChatModel.builder()
                .ollamaApi(ollamaApi)
                .defaultOptions(options)
                .build();
        return ChatClient.builder(chatModel).build();
    }
}
