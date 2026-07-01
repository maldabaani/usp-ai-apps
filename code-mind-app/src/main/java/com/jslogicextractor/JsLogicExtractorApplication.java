package com.jslogicextractor;

import com.jslogicextractor.config.BatchExtractionProperties;
import com.jslogicextractor.config.ChunkingProperties;
import com.jslogicextractor.config.EmbeddingProperties;
import com.jslogicextractor.config.ExtractionProperties;
import com.jslogicextractor.config.OllamaProperties;
import com.jslogicextractor.config.QaProperties;
import com.jslogicextractor.config.WatchProperties;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.properties.EnableConfigurationProperties;

@SpringBootApplication
@EnableConfigurationProperties({ExtractionProperties.class, BatchExtractionProperties.class, OllamaProperties.class,
        ChunkingProperties.class, EmbeddingProperties.class, WatchProperties.class, QaProperties.class})
public class JsLogicExtractorApplication {

    public static void main(String[] args) {
        SpringApplication.run(JsLogicExtractorApplication.class, args);
    }
}
