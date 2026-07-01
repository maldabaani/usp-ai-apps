package com.jslogicextractor.batch;

import com.anthropic.client.AnthropicClient;
import com.anthropic.client.okhttp.AnthropicOkHttpClient;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/**
 * The Batches API has no Spring AI support (spring-ai-anthropic 1.1.7 exposes no batch client), so
 * batch mode talks to Anthropic directly via the raw Java SDK, reusing the same API key as the
 * sync path's Spring AI ChatClient.
 */
@Configuration
public class AnthropicBatchClientConfig {

    @Bean
    public AnthropicClient anthropicClient(@Value("${spring.ai.anthropic.api-key}") String apiKey) {
        return AnthropicOkHttpClient.builder().apiKey(apiKey).build();
    }
}
