"""GET/approve/reject /lessons: human review of candidate rules proposed from run friction."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from devcrew.config import DEVCREW_DIR
from devcrew.db.models import Lesson, LessonSource, LessonStatus
from devcrew.tools.catalog import RulesCatalog
from tests.devcrew.api_harness import Api, api
from tests.devcrew.graph_harness import Harness, make_harness

RULE_TEXT = "Catch driver exceptions at the service boundary."


def isolated_rules_harness(tmp_path: Path) -> Harness:
    """A harness whose RulesCatalog points at a throwaway copy of the real rules files, so
    approving a lesson in a test never writes into the repo's own devcrew_resources/rules."""
    dest = tmp_path / "rules"
    shutil.copytree(DEVCREW_DIR / "rules", dest)
    h = make_harness(tmp_path)
    h.deps.rules = RulesCatalog(dest)
    return h


async def add_lesson(a: Api, **overrides: Any) -> Lesson:
    defaults: dict[str, Any] = dict(
        run_id="run-1",
        task_id="T1",
        stack="python",
        source=LessonSource.COORDINATOR_ESCALATION,
        rule_text=RULE_TEXT,
        evidence="Coordinator retry (agent_error): ... -> ...",
    )
    return await a.container.lessons.add_lesson(**{**defaults, **overrides})


async def test_list_lessons_filters_by_status(tmp_path: Path) -> None:
    async with api(tmp_path) as a:
        pending = await add_lesson(a)
        approved = await add_lesson(a, task_id="T2")
        await a.container.lessons.decide_lesson(
            approved.id, status=LessonStatus.APPROVED, rule_id="PY-901"
        )

        all_rows = (await a.client.get("/lessons")).json()
        assert {r["id"] for r in all_rows} == {pending.id, approved.id}

        only_pending = (await a.client.get("/lessons", params={"status": "pending"})).json()
        assert [r["id"] for r in only_pending] == [pending.id]


async def test_approve_lesson_appends_the_rule_and_marks_it_approved(tmp_path: Path) -> None:
    h = isolated_rules_harness(tmp_path)
    rules_path = h.deps.rules.rules_dir / "python.md"
    async with api(tmp_path, harness=h) as a:
        lesson = await add_lesson(a)
        assert RULE_TEXT not in rules_path.read_text(encoding="utf-8")

        resp = await a.client.post(f"/lessons/{lesson.id}/approve")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "approved"
        assert body["rule_id"] is not None and body["rule_id"].startswith("PY-9")
        assert RULE_TEXT in rules_path.read_text(encoding="utf-8")


async def test_reject_lesson_marks_it_rejected_without_touching_rules(tmp_path: Path) -> None:
    h = isolated_rules_harness(tmp_path)
    rules_path = h.deps.rules.rules_dir / "python.md"
    async with api(tmp_path, harness=h) as a:
        lesson = await add_lesson(a)
        before = rules_path.read_text(encoding="utf-8")

        resp = await a.client.post(f"/lessons/{lesson.id}/reject")
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "rejected"
        assert resp.json()["rule_id"] is None
        assert rules_path.read_text(encoding="utf-8") == before


async def test_approving_an_already_decided_lesson_conflicts(tmp_path: Path) -> None:
    h = isolated_rules_harness(tmp_path)
    async with api(tmp_path, harness=h) as a:
        lesson = await add_lesson(a)
        first = await a.client.post(f"/lessons/{lesson.id}/approve")
        assert first.status_code == 200, first.text
        second = await a.client.post(f"/lessons/{lesson.id}/approve")
        assert second.status_code == 409


async def test_approving_an_unknown_lesson_404s(tmp_path: Path) -> None:
    async with api(tmp_path) as a:
        resp = await a.client.post("/lessons/999999/approve")
        assert resp.status_code == 404
