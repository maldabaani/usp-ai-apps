package com.jslogicextractor.web;

import com.jslogicextractor.qa.QaAnswer;

import java.util.List;

public record QaResponse(
        String answer,
        List<String> sourceFiles
) {

    public static QaResponse from(QaAnswer answer) {
        return new QaResponse(answer.answer(), answer.sourceFiles());
    }
}
