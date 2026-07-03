"""Ported from com.jslogicextractor.prompt.LogicExtractionPromptTemplatesTest,
plus a golden-text test comparing this port's output byte-for-byte against
the real Java app's rendered output (captured via a one-off scratch program
run against the compiled Java classes -- see fixtures/golden_prompts.json).
This closes off prompt-drift risk independent of any LLM call.
"""
import json
from pathlib import Path

from codemind import prompts
from codemind.models import Language, SourceFile

_FIXTURES_DIR = Path(__file__).parent / "fixtures"


def test_substitutes_file_metadata_and_content():
    file = SourceFile(Path("/repo/src/index.js"), "src/index.js", "function add(a, b) { return a + b; }", 42)

    system, user = prompts.build_extraction_messages(file)
    rendered = system + user

    assert "src/index.js" in rendered
    assert "function add(a, b) { return a + b; }" in rendered


def test_survives_content_containing_angle_brackets_and_braces():
    file = SourceFile(
        Path("/repo/a.tsx"), "a.tsx", "const ok = (x: Array<string>) => x.length > 0 && <div>{x}</div>;", 10
    )

    _, user = prompts.build_extraction_messages(file)

    assert "Array<string>" in user
    assert "<div>{x}</div>" in user


def test_uses_language_specific_code_fence():
    js_file = SourceFile(Path("/repo/app.js"), "app.js", "const x = 1;", 10)
    py_file = SourceFile(Path("/repo/app.py"), "app.py", "x = 1", 5)
    java_file = SourceFile(Path("/repo/App.java"), "App.java", "class App {}", 12)

    assert "```javascript" in prompts.build_extraction_messages(js_file)[1]
    assert "```python" in prompts.build_extraction_messages(py_file)[1]
    assert "```java" in prompts.build_extraction_messages(java_file)[1]


def test_render_static_system_skeleton_returns_different_text_per_language():
    js_skeleton = prompts.render_static_system_skeleton(Language.JAVASCRIPT)
    py_skeleton = prompts.render_static_system_skeleton(Language.PYTHON)

    assert "JavaScript" in js_skeleton
    assert "Python" in py_skeleton
    assert js_skeleton != py_skeleton


_GOLDEN_EXTENSIONS = {
    "JAVASCRIPT": ".js",
    "TYPESCRIPT": ".ts",
    "PYTHON": ".py",
    "JAVA": ".java",
    "KOTLIN": ".kt",
    "GO": ".go",
    "CSHARP": ".cs",
    "RUBY": ".rb",
    "RUST": ".rs",
    "PHP": ".php",
    "UNKNOWN": ".txt",
}
_GOLDEN_SAMPLE_CONTENT = "function sample(a, b) {\n  return a + b; // <div>{x}</div> Array<string>\n}\n"


def test_golden_text_matches_java_output_for_every_language():
    golden = json.loads((_FIXTURES_DIR / "golden_prompts.json").read_text())

    for lang in Language:
        expected = golden[lang.name]
        ext = _GOLDEN_EXTENSIONS[lang.name]
        rel_path = f"src/sample{ext}"
        file = SourceFile(Path(f"/repo/{rel_path}"), rel_path, _GOLDEN_SAMPLE_CONTENT, 100)

        system = prompts.render_static_system_skeleton(lang)
        user = prompts.render_user_content(file)

        assert system == expected["system"], f"system prompt drifted for {lang.name}"
        assert user == expected["user"], f"user content drifted for {lang.name}"
