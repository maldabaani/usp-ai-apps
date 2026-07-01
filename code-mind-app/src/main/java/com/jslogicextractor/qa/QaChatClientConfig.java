package com.jslogicextractor.qa;

import com.jslogicextractor.config.QaProperties;
import org.springframework.ai.chat.client.ChatClient;
import org.springframework.ai.ollama.OllamaChatModel;
import org.springframework.ai.ollama.api.OllamaApi;
import org.springframework.ai.ollama.api.OllamaChatOptions;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/**
 * Produces the ChatClient used by the Ask Agent endpoint.
 * Set jsprocessor.qa.model=ollama to use a local Ollama model (fully offline, no API cost);
 * the default is claude which routes to the configured Anthropic model.
 */
@Configuration
public class QaChatClientConfig {

    @Bean("qaChatClient")
    public ChatClient qaChatClient(QaProperties props, ChatClient.Builder anthropicBuilder) {
        if ("ollama".equalsIgnoreCase(props.model())) {
            OllamaApi ollamaApi = OllamaApi.builder()
                    .baseUrl(props.ollamaBaseUrl())
                    .build();
            OllamaChatOptions options = OllamaChatOptions.builder()
                    .model(props.ollamaModel())
                    .numPredict(props.ollamaMaxTokens())
                    .build();
            OllamaChatModel chatModel = OllamaChatModel.builder()
                    .ollamaApi(ollamaApi)
                    .defaultOptions(options)
                    .build();
            return ChatClient.builder(chatModel).build();
        }
        return anthropicBuilder.build();
    }
}
