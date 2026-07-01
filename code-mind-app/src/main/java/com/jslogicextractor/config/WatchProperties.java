package com.jslogicextractor.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

@ConfigurationProperties(prefix = "jsprocessor.watch")
public record WatchProperties(
        boolean enabled,
        String directory,
        long quietPeriodMillis
) {

    public WatchProperties {
        if (directory == null || directory.isBlank()) {
            directory = "./watch-input";
        }
        if (quietPeriodMillis <= 0) {
            quietPeriodMillis = 500;
        }
    }
}
