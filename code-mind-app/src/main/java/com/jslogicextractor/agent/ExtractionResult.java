package com.jslogicextractor.agent;

import com.jslogicextractor.scanner.SourceFile;

public record ExtractionResult(
        String relativePath,
        String agentName,
        boolean success,
        boolean skipped,
        String content,
        String errorMessage,
        long durationMillis,
        Integer promptTokens,
        Integer completionTokens
) {

    public static ExtractionResult success(SourceFile file, String agentName, String content,
                                            long durationMillis, Integer promptTokens, Integer completionTokens) {
        return new ExtractionResult(file.relativePath(), agentName, true, false, content, null,
                durationMillis, promptTokens, completionTokens);
    }

    public static ExtractionResult failure(SourceFile file, String agentName, String errorMessage,
                                            long durationMillis) {
        return new ExtractionResult(file.relativePath(), agentName, false, false, null, errorMessage,
                durationMillis, null, null);
    }

    public static ExtractionResult skipped(SourceFile file, String agentName, String reason) {
        return new ExtractionResult(file.relativePath(), agentName, true, true, null, reason,
                0, null, null);
    }
}
