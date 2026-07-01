package com.jslogicextractor.web;

import com.jslogicextractor.output.OutputFileSnapshotService;

import java.time.Instant;

public record OutputFileResponse(
        String relativePath,
        long sizeBytes,
        Instant modifiedAt
) {

    public static OutputFileResponse from(OutputFileSnapshotService.OutputFile file) {
        return new OutputFileResponse(file.relativePath(), file.sizeBytes(), file.modifiedAt());
    }
}
