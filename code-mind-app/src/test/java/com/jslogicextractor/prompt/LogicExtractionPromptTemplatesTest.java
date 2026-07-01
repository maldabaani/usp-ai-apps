package com.jslogicextractor.prompt;

import com.jslogicextractor.scanner.SourceFile;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.ai.chat.prompt.Prompt;

import java.nio.file.Path;

import static org.assertj.core.api.Assertions.assertThat;

class LogicExtractionPromptTemplatesTest {

    private LogicExtractionPromptTemplates templates;

    @BeforeEach
    void setUp() {
        templates = new LogicExtractionPromptTemplates();
    }

    @Test
    void substitutesFileMetadataAndContent() {
        SourceFile file = new SourceFile(Path.of("/repo/src/index.js"), "src/index.js",
                "function add(a, b) { return a + b; }", 42);

        Prompt prompt = templates.buildExtractionPrompt(file);
        String rendered = prompt.getContents();

        assertThat(rendered).contains("src/index.js");
        assertThat(rendered).contains("function add(a, b) { return a + b; }");
    }

    @Test
    void survivesContentContainingAngleBracketsAndBraces() {
        SourceFile file = new SourceFile(Path.of("/repo/a.tsx"), "a.tsx",
                "const ok = (x: Array<string>) => x.length > 0 && <div>{x}</div>;", 10);

        Prompt prompt = templates.buildExtractionPrompt(file);

        assertThat(prompt.getContents()).contains("Array<string>").contains("<div>{x}</div>");
    }

    @Test
    void usesLanguageSpecificCodeFence() {
        SourceFile jsFile = new SourceFile(Path.of("/repo/app.js"), "app.js", "const x = 1;", 10);
        SourceFile pyFile = new SourceFile(Path.of("/repo/app.py"), "app.py", "x = 1", 5);
        SourceFile javaFile = new SourceFile(Path.of("/repo/App.java"), "App.java", "class App {}", 12);

        assertThat(templates.buildExtractionPrompt(jsFile).getContents()).contains("```javascript");
        assertThat(templates.buildExtractionPrompt(pyFile).getContents()).contains("```python");
        assertThat(templates.buildExtractionPrompt(javaFile).getContents()).contains("```java");
    }

    @Test
    void renderStaticSystemSkeletonReturnsDifferentTextPerLanguage() {
        String jsSkeleton = templates.renderStaticSystemSkeleton(com.jslogicextractor.scanner.Language.JAVASCRIPT);
        String pySkeleton = templates.renderStaticSystemSkeleton(com.jslogicextractor.scanner.Language.PYTHON);

        assertThat(jsSkeleton).contains("JavaScript");
        assertThat(pySkeleton).contains("Python");
        assertThat(jsSkeleton).isNotEqualTo(pySkeleton);
    }
}
