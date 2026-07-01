package com.jslogicextractor.output;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.jslogicextractor.agent.ExtractionResult;
import com.jslogicextractor.orchestration.ExtractionJob;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.Map;

@Component
public class FileSystemExtractionResultWriter implements ExtractionResultWriter {

    private static final Logger log = LoggerFactory.getLogger(FileSystemExtractionResultWriter.class);

    private final ObjectMapper objectMapper;

    public FileSystemExtractionResultWriter(ObjectMapper objectMapper) {
        this.objectMapper = objectMapper;
    }

    @Override
    public boolean exists(ExtractionJob job, String relativePath) {
        return Files.exists(job.outputDirectory().resolve(relativePath + ".json"));
    }

    @Override
    public void write(ExtractionJob job, ExtractionResult result) {
        Path outputFile = job.outputDirectory().resolve(result.relativePath() + ".json");
        try {
            Files.createDirectories(outputFile.getParent());
            Files.writeString(outputFile,
                    objectMapper.writerWithDefaultPrettyPrinter().writeValueAsString(result),
                    StandardCharsets.UTF_8);
        } catch (IOException e) {
            log.error("Failed to write extraction result for {}: {}", result.relativePath(), e.getMessage());
        }
    }

    @Override
    public void writeSummary(ExtractionJob job) {
        Path summaryFile = job.outputDirectory().resolve("_summary.json");
        Map<String, Object> summary = new LinkedHashMap<>();
        summary.put("jobId", job.id().toString());
        summary.put("phase", job.phase().name());
        summary.put("repositoryRoot", job.repositoryRoot().toString());
        summary.put("totalFiles", job.totalCount());
        summary.put("succeeded", job.succeededCount());
        summary.put("failed", job.failedCount());
        summary.put("skipped", job.skippedCount());
        summary.put("createdAt", job.createdAt().toString());
        summary.put("finishedAt", job.finishedAt() != null ? job.finishedAt().toString() : null);
        summary.put("failureReason", job.failureReason());

        try {
            Files.createDirectories(job.outputDirectory());
            Files.writeString(summaryFile,
                    objectMapper.writerWithDefaultPrettyPrinter().writeValueAsString(summary),
                    StandardCharsets.UTF_8);
        } catch (IOException e) {
            log.error("Failed to write job summary for job {}: {}", job.id(), e.getMessage());
        }
    }
}
