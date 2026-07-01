package com.jslogicextractor.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

@ConfigurationProperties(prefix = "jsprocessor.chunking")
public record ChunkingProperties(
        boolean enabled,
        int maxLinesPerChunk
) {

    public ChunkingProperties {
        if (maxLinesPerChunk <= 0) {
            maxLinesPerChunk = 1800;
        }
    }
}
