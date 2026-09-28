"""Consistency check of the Python example code in a design document.

Developers copy the design's contracts; the Reviewer then rejects what does not work. In a real
run the design's router used `BookmarkService = Depends()` although no `BookmarkService` was
defined, and a function stub had no body: the task failed review three times. This check finds
such problems so the Architect fixes them before anyone codes against them.
"""

from __future__ import annotations

import ast
import builtins
import re

_PY_BLOCK_RE = re.compile(r"```(?:python|py)[ \t]*\n(.*?)```", re.DOTALL | re.IGNORECASE)
_BUILTINS = set(dir(builtins))
MAX_PROBLEMS = 8


def _defined(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store | ast.Del):
            names.add(node.id)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.alias):
            names.add((node.asname or node.name).split(".")[0])
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
    return names


def python_snippet_problems(doc: str) -> list[str]:
    """Syntax errors per block, and names used but defined or imported in no block."""
    problems: list[str] = []
    trees: list[tuple[int, ast.AST]] = []
    for index, match in enumerate(_PY_BLOCK_RE.finditer(doc), start=1):
        try:
            trees.append((index, ast.parse(match.group(1))))
        except SyntaxError as exc:
            problems.append(
                f"python block {index}: syntax error at line {exc.lineno}: {exc.msg} "
                "(give stubs a body, e.g. `...`)"
            )
    defined = set().union(*(_defined(tree) for _, tree in trees)) if trees else set()
    for index, tree in trees:
        missing = sorted(
            {
                node.id
                for node in ast.walk(tree)
                if isinstance(node, ast.Name)
                and isinstance(node.ctx, ast.Load)
                and node.id not in defined
                and node.id not in _BUILTINS
            }
        )
        if missing:
            problems.append(
                f"python block {index}: {', '.join(missing)} used but never defined or imported "
                "in the design (define it in a contract or remove it)"
            )
    return problems[:MAX_PROBLEMS]
