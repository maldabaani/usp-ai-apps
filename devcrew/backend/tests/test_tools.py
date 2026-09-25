from __future__ import annotations

from pathlib import Path

import pytest

from app.config import DEVCREW_DIR
from app.tools.base import ToolError
from app.tools.catalog import RulesCatalog, TemplatesCatalog
from app.tools.git import GitRepo
from app.tools.workspace import Workspace, WriteFileArgs, is_test_path, write_file_tool


@pytest.mark.parametrize("path", ["../x", "/etc/passwd", "a/../../x", ".git/hooks/pre-commit", ""])
def test_workspace_rejects_escapes(tmp_path: Path, path: str) -> None:
    with pytest.raises(ToolError):
        Workspace(tmp_path).resolve(path)


def test_workspace_rejects_symlink_escape(tmp_path: Path) -> None:
    (tmp_path / "ws").mkdir()
    (tmp_path / "ws" / "link").symlink_to(tmp_path)
    with pytest.raises(ToolError, match="escapes"):
        Workspace(tmp_path / "ws").resolve("link/secret")


def test_read_write_list(tmp_path: Path) -> None:
    ws = Workspace(tmp_path)
    ws.write("app/a.py", "\n".join(f"line{i}" for i in range(1, 11)))
    out = ws.read("app/a.py", start_line=3, max_lines=2)
    assert "    3| line3" in out and "    4| line4" in out and "start_line=5" in out
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "x.js").write_text("x")
    assert ws.list(".") == ["app/a.py"]


@pytest.mark.parametrize(
    ("path", "stack", "ok"),
    [
        ("tests/test_api.py", "python", True),
        ("tests/conftest.py", "python", True),
        ("app/main.py", "python", False),
        ("src/test/java/com/x/FooTest.java", "java", True),
        ("src/main/java/com/x/Foo.java", "java", False),
        ("src/app/todo/todo.component.spec.ts", "angular", True),
        ("src/app/todo/todo.component.ts", "angular", False),
    ],
)
def test_test_path_filter(path: str, stack: str, ok: bool) -> None:
    assert is_test_path(path, [stack]) is ok


async def test_write_file_tool_enforces_filter(tmp_path: Path) -> None:
    tool = write_file_tool(Workspace(tmp_path), lambda p: is_test_path(p, ["python"]))
    assert tool.handler is not None
    with pytest.raises(ToolError, match="not allowed"):
        await tool.handler(WriteFileArgs(path="app/main.py", content="x"))
    assert "wrote" in await tool.handler(WriteFileArgs(path="tests/test_a.py", content="x"))


def test_rules_catalog_reads_rule_ids() -> None:
    rules = RulesCatalog(DEVCREW_DIR / "rules")
    assert rules.stacks() == ["angular", "java", "python"]
    ids = rules.rule_ids(["python"])
    assert {"PY-001", "PY-030"} <= ids and not any(i.startswith("JAVA") for i in ids)
    with pytest.raises(ToolError):
        rules.read("cobol")


def test_templates_catalog() -> None:
    templates = TemplatesCatalog(DEVCREW_DIR / "templates")
    assert templates.ids_by_stack() == {
        "angular-standalone": "angular",
        "java-spring-boot": "java",
        "python-fastapi": "python",
    }


async def test_git_squash_merge_one_commit_per_task(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    await repo.init()
    (tmp_path / "a.txt").write_text("a\n")
    await repo.commit_all("init")
    await repo.checkout("feature", create_from="main")
    for i in range(3):
        (tmp_path / "b.txt").write_text(f"{i}\n")
        await repo.commit_all(f"wip {i}")
    assert await repo.changed_files("main", "feature") == ["b.txt"]
    assert "+2" in await repo.diff("main", "feature")
    result = await repo.squash_merge("feature", "main", "T1: feature")
    assert result.ok and result.commit
    log = (await repo.run("log", "--format=%s")).split("\n")
    assert log[:2] == ["T1: feature", "init"]
    assert await repo.commit_all("nothing") is None


async def test_git_merge_conflict_is_reported_and_aborted(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    await repo.init()
    (tmp_path / "f.txt").write_text("base\n")
    await repo.commit_all("init")
    await repo.checkout("t1", create_from="main")
    (tmp_path / "f.txt").write_text("t1\n")
    await repo.commit_all("t1")
    await repo.checkout("main")
    (tmp_path / "f.txt").write_text("main\n")
    await repo.commit_all("main change")
    result = await repo.squash_merge("t1", "main", "T1")
    assert not result.ok and result.conflicts == ["f.txt"]
    assert (tmp_path / "f.txt").read_text() == "main\n"
    assert not (await repo.run("status", "--porcelain")).strip()


async def test_git_hooks_are_disabled(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path)
    await repo.init()
    hook = tmp_path / ".git" / "hooks" / "pre-commit"
    marker = tmp_path.parent / f"{tmp_path.name}-hook-ran"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\n")
    hook.chmod(0o755)
    (tmp_path / "a").write_text("a")
    await repo.commit_all("x")
    assert not marker.exists()
