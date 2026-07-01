# Prompt resources

`logic-extraction-prompt.st` is the prompt sent to Claude for every source file. Replace its
contents with the team's existing, validated logic-extraction prompt — that swap is the only
change needed; no Java code has to change.

Rules for the replacement text:

- Keep the placeholders `<fileName>`, `<filePath>`, and `<fileContent>` (drop whichever ones the
  real prompt doesn't need) — `LogicExtractionPromptTemplates` fills them in per file before the
  request is sent.
- This template uses `<` and `>` as the variable delimiters, not the default `{` `}`, so the
  prompt can contain a literal JSON response schema without escaping every brace. Avoid unrelated
  `<...>` text in the prompt body (e.g. HTML-looking snippets) — the renderer will try to parse it
  as a variable reference.
- If the real prompt needs inputs beyond these three, add them to
  `LogicExtractionPromptTemplates.buildExtractionPrompt(...)` in `com.jslogicextractor.prompt`.
