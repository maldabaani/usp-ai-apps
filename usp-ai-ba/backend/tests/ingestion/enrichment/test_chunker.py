"""Ported from com.jslogicextractor.scanner.LargeFileChunkerTest."""
from pathlib import Path

from ingestion.enrichment import chunker

_ABSOLUTE_PATH = Path("/repo/big.js")


def test_content_under_target_line_count_returns_single_chunk():
    chunks = chunker.chunk(_ABSOLUTE_PATH, "big.js", "const a = 1;\nconst b = 2;", 100)

    assert len(chunks) == 1
    assert chunks[0].relative_path == "big.js/part-0001.js"
    assert chunks[0].content == "const a = 1;\nconst b = 2;"


def test_splits_at_safe_boundaries_near_target_line_count():
    content = "const a = 1;\nconst b = 2;\nconst c = 3;\nconst d = 4;"

    chunks = chunker.chunk(_ABSOLUTE_PATH, "big.js", content, 2)

    assert [c.relative_path for c in chunks] == ["big.js/part-0001.js", "big.js/part-0002.js"]
    assert chunks[0].content == "const a = 1;\nconst b = 2;"
    assert chunks[1].content == "const c = 3;\nconst d = 4;"


def test_waits_for_safe_boundary_when_block_spans_target_line_count():
    content = "\n".join(
        [
            "function foo() {",
            "  doStuff();",
            "  doMore();",
            "}",
            "function bar() {",
            "  doStuff();",
            "}",
        ]
    )

    chunks = chunker.chunk(_ABSOLUTE_PATH, "big.js", content, 2)

    assert len(chunks) == 2
    assert chunks[0].content == "function foo() {\n  doStuff();\n  doMore();\n}"
    assert chunks[1].content == "function bar() {\n  doStuff();\n}"


def test_hard_cap_forces_a_cut_when_a_block_never_returns_to_depth_zero():
    content = "\n".join(
        [
            "function foo() {",
            "  a();",
            "  b();",
            "  c();",
            "  d();",
            "  e();",
            "}",
        ]
    )

    chunks = chunker.chunk(_ABSOLUTE_PATH, "big.js", content, 2)

    assert len(chunks) == 3
    assert chunks[0].content == "function foo() {\n  a();\n  b();\n  c();"
    assert chunks[1].content == "  d();\n  e();"
    assert chunks[2].content == "}"


def test_rejoining_chunks_reconstructs_original_content():
    content = "const a = 1;\nconst b = 2;\nconst c = 3;\nconst d = 4;"

    chunks = chunker.chunk(_ABSOLUTE_PATH, "big.js", content, 2)

    rejoined = "\n".join(c.content for c in chunks)
    assert rejoined == content


def test_single_line_file_with_no_line_breaks_is_sent_as_one_chunk():
    content = "x" * 500

    chunks = chunker.chunk(_ABSOLUTE_PATH, "tiny.js", content, 100)

    assert len(chunks) == 1
    assert chunks[0].relative_path == "tiny.js/part-0001.js"
    assert chunks[0].content == content
