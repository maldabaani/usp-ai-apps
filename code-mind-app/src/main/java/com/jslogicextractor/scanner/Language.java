package com.jslogicextractor.scanner;

import java.util.Locale;

public enum Language {
    JAVASCRIPT("JavaScript", "javascript", ".js", ".jsx", ".mjs", ".cjs"),
    TYPESCRIPT("TypeScript", "typescript", ".ts", ".tsx"),
    PYTHON("Python", "python", ".py", ".pyw"),
    JAVA("Java", "java", ".java"),
    KOTLIN("Kotlin", "kotlin", ".kt", ".kts"),
    GO("Go", "go", ".go"),
    CSHARP("C#", "csharp", ".cs"),
    RUBY("Ruby", "ruby", ".rb"),
    RUST("Rust", "rust", ".rs"),
    PHP("PHP", "php", ".php"),
    UNKNOWN("Unknown", "text");

    private final String displayName;
    private final String codeFence;
    private final String[] extensions;

    Language(String displayName, String codeFence, String... extensions) {
        this.displayName = displayName;
        this.codeFence = codeFence;
        this.extensions = extensions;
    }

    public String displayName() {
        return displayName;
    }

    public String codeFence() {
        return codeFence;
    }

    public static Language fromPath(String relativePath) {
        if (relativePath == null) return UNKNOWN;
        String lower = relativePath.toLowerCase(Locale.ROOT);
        for (Language lang : values()) {
            for (String ext : lang.extensions) {
                if (lower.endsWith(ext)) return lang;
            }
        }
        return UNKNOWN;
    }
}
