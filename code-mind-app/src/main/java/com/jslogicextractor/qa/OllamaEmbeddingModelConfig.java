package com.jslogicextractor.qa;

import com.jslogicextractor.config.EmbeddingProperties;
import org.springframework.ai.embedding.EmbeddingModel;
import org.springframework.ai.ollama.OllamaEmbeddingModel;
import org.springframework.ai.ollama.api.OllamaApi;
import org.springframework.ai.ollama.api.OllamaEmbeddingOptions;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/**
 * The OllamaApi/OllamaEmbeddingModel here are built directly rather than relying on Spring AI's own
 * OllamaEmbeddingAutoConfiguration, mirroring OllamaChatClientConfig: it keeps this feature entirely
 * opt-in behind jsprocessor.embedding.enabled, with no spring.ai.model.embedding wiring to manage.
 */
@Configuration
@ConditionalOnProperty(prefix = "jsprocessor.embedding", name = "enabled", havingValue = "true")
public class OllamaEmbeddingModelConfig {

    @Bean
    public EmbeddingModel embeddingModel(EmbeddingProperties properties) {
        OllamaApi ollamaApi = OllamaApi.builder()
                .baseUrl(properties.baseUrl())
                .build();
        OllamaEmbeddingOptions options = OllamaEmbeddingOptions.builder()
                .model(properties.model())
                .build();
        return OllamaEmbeddingModel.builder()
                .ollamaApi(ollamaApi)
                .defaultOptions(options)
                .build();
    }
}
