package com.jslogicextractor.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

@ConfigurationProperties(prefix = "jsprocessor.embedding")
public record EmbeddingProperties(
        boolean enabled,
        String baseUrl,
        String model
) {

    public EmbeddingProperties {
        if (baseUrl == null || baseUrl.isBlank()) {
            baseUrl = "http://localhost:11434";
        }
        if (model == null || model.isBlank()) {
            model = "nomic-embed-text";
        }
    }
}
