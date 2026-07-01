package com.jslogicextractor.qa;

import java.util.List;

public record QaAnswer(String answer, List<String> sourceFiles) {
}
