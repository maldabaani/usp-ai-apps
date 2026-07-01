package com.jslogicextractor.web;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Positive;

public record StartJobRequest(
        @NotBlank String repositoryPath,
        String outputDirectory,
        @Positive Integer maxConcurrency,
        String executionMode
) {
}
