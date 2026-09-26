from __future__ import annotations

import textwrap

import pytest

from app.rag.chunking import Chunk, ChunkConfig, chunk_file, detect_language


def covered(chunks: list[Chunk], text: str) -> None:
    """Every non-blank line is in some chunk, and chunk text matches its line range."""
    lines = text.splitlines()
    seen: set[int] = set()
    for c in chunks:
        assert c.text == "\n".join(lines[c.start_line - 1 : c.end_line])
        seen.update(range(c.start_line, c.end_line + 1))
    missing = [i + 1 for i, line in enumerate(lines) if line.strip() and i + 1 not in seen]
    assert not missing, f"uncovered lines {missing}"


def symbols(chunks: list[Chunk]) -> list[str]:
    return [c.symbol for c in chunks]


PY = textwrap.dedent('''\
    """Module doc."""
    from fastapi import APIRouter

    router = APIRouter()


    @router.get("/todos")
    def list_todos() -> list[str]:
        return []


    class TodoService:
        def __init__(self) -> None:
            self.items: list[str] = []

        async def add(self, item: str) -> None:
            self.items.append(item)
''')


def test_python_splits_by_definition_with_decorators() -> None:
    chunks = chunk_file("app/routers/todos.py", PY)
    covered(chunks, PY)
    assert symbols(chunks) == ["", "def list_todos", "class TodoService"]
    route = chunks[1]
    assert route.start_line == 7 and route.text.startswith("@router.get")
    assert all(c.language == "python" for c in chunks)


def test_large_python_class_is_split_into_methods() -> None:
    methods = "\n".join(f"    def m{i}(self) -> int:\n        return {i}\n" for i in range(30))
    source = f"class Big:\n    x = 1\n\n{methods}"
    chunks = chunk_file("big.py", source, ChunkConfig(max_lines=40))
    covered(chunks, source)
    assert symbols(chunks)[:3] == ["class Big", "def Big.m0", "def Big.m1"]
    assert all(c.end_line - c.start_line < 40 for c in chunks)


def test_python_syntax_error_falls_back_to_windows() -> None:
    source = "def broken(:\n" + "x = 1\n" * 100
    chunks = chunk_file("bad.py", source, ChunkConfig(window_lines=30, overlap_lines=5))
    covered(chunks, source)
    assert [(c.start_line, c.end_line) for c in chunks][:3] == [(1, 30), (26, 55), (51, 80)]


JAVA = textwrap.dedent("""\
    package com.devcrew.app.controller;

    import org.springframework.web.bind.annotation.GetMapping;

    @RestController
    @RequestMapping("/api/todos")
    public class TodoController {

        private final TodoService service;

        public TodoController(TodoService service) {
            this.service = service;
        }

        @GetMapping
        public List<TodoDto> list() {
            String s = "not a } brace";
            // nor } this
            return service.findAll();
        }
    }
""")


def test_java_class_with_annotations_and_tricky_braces() -> None:
    chunks = chunk_file("src/main/java/TodoController.java", JAVA)
    covered(chunks, JAVA)
    assert symbols(chunks) == ["", "class TodoController"]
    assert chunks[1].start_line == 5 and chunks[1].end_line == 21


def test_large_java_class_is_split_into_members() -> None:
    chunks = chunk_file("T.java", JAVA, ChunkConfig(max_lines=10, window_lines=8))
    covered(chunks, JAVA)
    syms = symbols(chunks)
    assert "TodoController > method TodoController" in syms
    assert "TodoController > method list" in syms
    lst = next(c for c in chunks if c.symbol.endswith("method list"))
    assert lst.text.lstrip().startswith("@GetMapping")


TS = textwrap.dedent("""\
    import { Component, signal } from '@angular/core';

    @Component({
      selector: 'app-todo',
      template: '<p>{{ title() }}</p>',
    })
    export class TodoComponent {
      readonly title = signal('todos');
    }

    export function add(a: number, b: number): number {
      return a + b;
    }

    export const API_URL = '/api';
""")


def test_typescript_units_and_multiline_decorators() -> None:
    chunks = chunk_file("src/app/todo.component.ts", TS)
    covered(chunks, TS)
    assert symbols(chunks) == ["", "class TodoComponent", "function add", "const API_URL"]
    assert chunks[1].start_line == 3  # decorator attached to the class


SPEC = textwrap.dedent("""\
    import { TestBed } from '@angular/core/testing';

    describe('TodoComponent', () => {
      beforeEach(() => {
        TestBed.configureTestingModule({});
      });

      it('creates', () => {
        expect(true).toBeTrue();
      });

      it('renders', () => {
        expect(1).toBe(1);
      });
    });
""")


def test_jasmine_describe_and_it_blocks() -> None:
    chunks = chunk_file("todo.component.spec.ts", SPEC)
    covered(chunks, SPEC)
    assert symbols(chunks) == ["", "describe 'TodoComponent'"]
    split = chunk_file("todo.component.spec.ts", SPEC, ChunkConfig(max_lines=6, window_lines=6))
    covered(split, SPEC)
    assert "TodoComponent' > it 'renders'" in " | ".join(symbols(split))


def test_markdown_split_by_headings() -> None:
    md = "intro\n# Title\ntext\n## Contracts\nGET /todos\n## Errors\n404\n"
    chunks = chunk_file("docs/design.md", md)
    covered(chunks, md)
    assert symbols(chunks) == ["", "section Title", "section Contracts", "section Errors"]


def test_unknown_files_use_windows_and_empty_files_have_no_chunks() -> None:
    text = "\n".join(f"key{i}: v" for i in range(10))
    assert [(c.start_line, c.end_line) for c in chunk_file("a.yaml", text)] == [(1, 10)]
    assert chunk_file("empty.py", "") == []
    assert chunk_file("blank.py", "\n\n  \n") == []


@pytest.mark.parametrize(
    ("path", "lang"),
    [
        ("a/b.py", "python"),
        ("X.java", "java"),
        ("c.spec.ts", "typescript"),
        ("Dockerfile", "dockerfile"),
        ("README.md", "markdown"),
        ("x.unknown", "text"),
    ],
)
def test_detect_language(path: str, lang: str) -> None:
    assert detect_language(path) == lang
