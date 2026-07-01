package com.jslogicextractor.web;

import jakarta.validation.constraints.NotBlank;

public record QaRequest(
        @NotBlank String question
) {
}
