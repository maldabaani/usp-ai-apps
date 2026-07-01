package com.jslogicextractor.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

@ConfigurationProperties(prefix = "jsprocessor.ollama")
public record OllamaProperties(
        boolean enabled,
        String baseUrl,
        String model,
        int maxTokens,
        double temperature,
        int numCtx
) {

    public OllamaProperties {
        if (baseUrl == null || baseUrl.isBlank()) {
            baseUrl = "http://localhost:11434";
        }
        if (model == null || model.isBlank()) {
            model = "qwen2.5:14b";
        }
        if (maxTokens <= 0) {
            maxTokens = 1500;
        }
        if (numCtx <= 0) {
            numCtx = 8192;
        }
    }
}
