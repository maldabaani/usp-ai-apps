package com.jslogicextractor.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

@ConfigurationProperties(prefix = "jsprocessor.qa")
public record QaProperties(
        String model,
        String ollamaBaseUrl,
        String ollamaModel,
        int ollamaMaxTokens
) {

    public QaProperties {
        if (model == null || model.isBlank()) model = "claude";
        if (ollamaBaseUrl == null || ollamaBaseUrl.isBlank()) ollamaBaseUrl = "http://localhost:11434";
        if (ollamaModel == null || ollamaModel.isBlank()) ollamaModel = "qwen2.5:14b";
        if (ollamaMaxTokens <= 0) ollamaMaxTokens = 4096;
    }
}
