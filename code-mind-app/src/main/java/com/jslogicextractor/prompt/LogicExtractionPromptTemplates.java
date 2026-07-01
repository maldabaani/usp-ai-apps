package com.jslogicextractor.prompt;

import com.jslogicextractor.scanner.Language;
import com.jslogicextractor.scanner.SourceFile;
import org.springframework.ai.chat.messages.SystemMessage;
import org.springframework.ai.chat.messages.UserMessage;
import org.springframework.ai.chat.prompt.Prompt;
import org.springframework.stereotype.Component;

import java.util.List;
import java.util.Map;

@Component
public class LogicExtractionPromptTemplates {

    private static final Map<Language, String> LANGUAGE_HINTS = Map.ofEntries(
            Map.entry(Language.JAVASCRIPT,
                    "Focus on event handlers, async flows, module exports, and business rules embedded in callbacks or promises."),
            Map.entry(Language.TYPESCRIPT,
                    "Focus on typed interfaces, generics, decorators, async flows, and business rules expressed through types and class methods."),
            Map.entry(Language.PYTHON,
                    "Focus on class methods, decorators, async/await patterns, data transformations, and domain logic in functions or modules."),
            Map.entry(Language.JAVA,
                    "Focus on class hierarchies, design patterns (repository, service, factory), annotations, exception handling, and domain methods."),
            Map.entry(Language.KOTLIN,
                    "Focus on data classes, extension functions, coroutines, sealed classes, and domain-layer logic."),
            Map.entry(Language.GO,
                    "Focus on function signatures, interfaces, goroutine coordination, error handling patterns, and business rules in handlers or services."),
            Map.entry(Language.CSHARP,
                    "Focus on class hierarchies, LINQ expressions, async/await patterns, attributes, and business rules in services or controllers."),
            Map.entry(Language.RUBY,
                    "Focus on modules, mixins, blocks/procs, model callbacks and associations, and domain logic in service objects or models."),
            Map.entry(Language.RUST,
                    "Focus on trait implementations, ownership patterns, error handling with Result/Option, and business logic in structs and enums."),
            Map.entry(Language.PHP,
                    "Focus on class hierarchies, framework conventions, model associations, middleware, and business rules in service classes.")
    );

    private static final String DEFAULT_HINT =
            "Focus on the business logic, data flows, domain rules, and key abstractions expressed in the code.";

    private static final String OUTPUT_INSTRUCTION =
            "Extract the business logic of the file above and respond with JSON only (no markdown fences, no commentary) using this shape:\n" +
            "{\"file\": \"<filePath>\", \"summary\": \"one paragraph summary\", " +
            "\"rules\": [{\"name\": \"...\", \"description\": \"...\", \"conditions\": [\"...\"], \"actions\": [\"...\"]}], " +
            "\"dependencies\": [\"...\"]}";

    public Prompt buildExtractionPrompt(SourceFile file) {
        Language lang = Language.fromPath(file.relativePath());
        return new Prompt(List.of(
                new SystemMessage(buildSystemPrompt(lang)),
                new UserMessage(buildUserContent(file, lang))
        ));
    }

    public String renderStaticSystemSkeleton() {
        return renderStaticSystemSkeleton(Language.JAVASCRIPT);
    }

    public String renderStaticSystemSkeleton(Language lang) {
        return buildSystemPrompt(lang);
    }

    public String renderUserContent(SourceFile file) {
        Language lang = Language.fromPath(file.relativePath());
        return buildUserContent(file, lang);
    }

    private String buildSystemPrompt(Language lang) {
        String hint = LANGUAGE_HINTS.getOrDefault(lang, DEFAULT_HINT);
        return "You are a senior " + lang.displayName() + " engineer extracting business logic " +
                "from source code for documentation and migration purposes.\n\n" + hint;
    }

    private String buildUserContent(SourceFile file, Language lang) {
        return "File name: " + fileName(file) + "\n"
                + "File path: " + file.relativePath() + "\n\n"
                + "Source:\n```" + lang.codeFence() + "\n" + file.content() + "\n```\n\n"
                + OUTPUT_INSTRUCTION;
    }

    private String fileName(SourceFile file) {
        int idx = file.relativePath().lastIndexOf('/');
        return idx >= 0 ? file.relativePath().substring(idx + 1) : file.relativePath();
    }
}
