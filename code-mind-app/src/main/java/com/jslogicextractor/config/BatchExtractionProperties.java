package com.jslogicextractor.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

import java.time.Duration;

@ConfigurationProperties(prefix = "jsprocessor.batch")
public record BatchExtractionProperties(
        String model,
        int maxTokens,
        double temperature,
        Duration pollInterval,
        Duration pollTimeout,
        int maxRequestsPerBatch,
        long maxBatchBytes
) {

    public BatchExtractionProperties {
        if (model == null || model.isBlank()) {
            model = "claude-sonnet-4-5-20250929";
        }
        if (maxTokens <= 0) {
            maxTokens = 4096;
        }
        if (pollInterval == null) {
            pollInterval = Duration.ofSeconds(30);
        }
        if (pollTimeout == null) {
            pollTimeout = Duration.ofHours(26);
        }
        if (maxRequestsPerBatch <= 0) {
            // Anthropic's hard cap is 100,000 requests/batch; default conservatively below it.
            maxRequestsPerBatch = 10_000;
        }
        if (maxBatchBytes <= 0) {
            // Anthropic's hard cap is 256MB/batch; default with headroom for request overhead.
            maxBatchBytes = 200_000_000L;
        }
    }
}
