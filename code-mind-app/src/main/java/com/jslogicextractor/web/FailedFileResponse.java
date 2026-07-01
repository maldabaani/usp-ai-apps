package com.jslogicextractor.web;

import com.jslogicextractor.output.OutputFileSnapshotService;

public record FailedFileResponse(
        String relativePath,
        String errorMessage,
        long durationMillis
) {

    public static FailedFileResponse from(OutputFileSnapshotService.FailedFile f) {
        return new FailedFileResponse(f.relativePath(), f.errorMessage(), f.durationMillis());
    }
}
