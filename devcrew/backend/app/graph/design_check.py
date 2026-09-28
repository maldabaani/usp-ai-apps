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


MUTABLE_CALLS = {"dict", "list", "set", "defaultdict", "OrderedDict", "deque"}


def _is_mutable(value: ast.expr | None) -> bool:
    if isinstance(value, ast.Dict | ast.List | ast.Set | ast.DictComp | ast.ListComp):
        return True
    if isinstance(value, ast.Call):
        func = value.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        return name in MUTABLE_CALLS
    return False


def _rule_problems(index: int, tree: ast.Module) -> list[str]:
    """Stack-rule violations developers would copy from the design (a real design kept a
    module-level store with `global next_id` and Pydantic v1 `.dict()`: 16 rejected attempts)."""
    found: list[str] = []
    for stmt in tree.body:
        value = stmt.value if isinstance(stmt, ast.Assign | ast.AnnAssign) else None
        if _is_mutable(value):
            found.append(
                "module-level mutable state (PY-005): keep state in a class instance "
                "that routes receive with Depends"
            )
            break
    for node in ast.walk(tree):
        if isinstance(node, ast.Global):
            found.append(f"`global {', '.join(node.names)}` (PY-005): keep state in an instance")
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("dict", "parse_obj")
        ):
            found.append(
                f"Pydantic v1 `.{node.func.attr}()` (PY-003, Pydantic v2): use model_dump() or "
                "model_validate()"
            )
        elif isinstance(node, ast.ClassDef) and node.name == "Config":
            found.append(
                "Pydantic v1 `class Config` (PY-003, Pydantic v2): use model_config = ConfigDict()"
            )
    return [f"python block {index}: {msg}" for msg in dict.fromkeys(found)]


def python_snippet_problems(doc: str) -> list[str]:
    """Syntax errors, stack-rule violations, and names used but defined or imported nowhere."""
    problems: list[str] = []
    trees: list[tuple[int, ast.Module]] = []
    for index, match in enumerate(_PY_BLOCK_RE.finditer(doc), start=1):
        try:
            trees.append((index, ast.parse(match.group(1))))
        except SyntaxError as exc:
            problems.append(
                f"python block {index}: syntax error at line {exc.lineno}: {exc.msg} "
                "(give stubs a body, e.g. `...`)"
            )
    for index, tree in trees:
        problems += _rule_problems(index, tree)
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
