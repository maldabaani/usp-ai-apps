package com.jslogicextractor.scanner;

import java.nio.file.Path;

public record SourceFile(Path absolutePath, String relativePath, String content, long sizeBytes) {
}
