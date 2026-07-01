package com.jslogicextractor.config;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

@Configuration
public class AsyncExecutorConfig {

    // Only used to kick off whole jobs off the HTTP thread, so it scales with concurrent job count, not file count.
    @Bean(destroyMethod = "shutdown")
    public ExecutorService extractionExecutor() {
        return Executors.newCachedThreadPool();
    }
}
