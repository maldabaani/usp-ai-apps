#!/usr/bin/env python3
"""Run DevCrew from the terminal (no API/UI needed).

    cd devcrew/backend && . .venv/bin/activate
    python ../scripts/run_local.py "Build a FastAPI TODO API with CRUD and pytest tests"
    python ../scripts/run_local.py --resume <run_id>        # after Ctrl-C / crash
    python ../scripts/run_local.py --memory "..."           # no Postgres (not resumable)
    python ../scripts/run_local.py --auto "..."             # approve everything, auto-answer

Settings come from devcrew/.env and the environment (environment wins). When running on the
host, set OLLAMA_BASE_URL=http://localhost:11434 and a localhost DATABASE_URL.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import subprocess
import sys
import tempfile
import textwrap
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver

from app.config import DEVCREW_DIR, Settings
from app.db.repository import RunRepository, new_run_id
from app.db.session import create_engine, create_sessionmaker
from app.events.bus import EventBus
from app.events.store import InMemoryEventStore, PostgresEventStore
from app.events.types import Event, EventType
from app.graph.backbone import build_graph
from app.graph.checkpointer import postgres_checkpointer
from app.graph.factory import build_deps, build_llm
from app.graph.interrupts import ResumePayload
from app.graph.runner import PendingInterrupt, RunDriver, RunOutcome
from app.preflight import PreflightError, preflight

AUTO_ANSWER = "Use your best judgment and document the assumption."


# ------------------------------------------------------------------------------------ output
def fmt_event(e: Event) -> str | None:
    p = e.payload
    where = f"{e.node or ''}{f' [{e.task_id}]' if e.task_id else ''}"
    match e.type:
        case EventType.NODE_STARTED:
            return f"  ▶ {where}"
        case EventType.TOOL_CALL:
            args = json.dumps(p.get("args", {}))
            return f"      ↳ {p.get('tool')}({args[:100]}{'…' if len(args) > 100 else ''})"
        case EventType.TOOL_RESULT if p.get("tool") in ("index_codebase", "install_dependencies"):
            return f"      ✓ {p.get('tool')}: {str(p.get('result', ''))[:160]}"
        case EventType.TOOL_RESULT if not p.get("ok", True):
            return f"      ✗ {p.get('tool')}: {str(p.get('result', ''))[:160]}"
        case EventType.MERGE:
            return f"  ⇢ merged {e.task_id} into {p.get('into')}"
        case EventType.ERROR:
            return f"  ‼ {where}: {p.get('message')}"
        case EventType.STATUS:
            return f"── status: {p.get('status')}"
    return None


async def print_events(bus: EventBus, run_id: str) -> None:
    async for event in bus.subscribe(run_id):
        line = fmt_event(event)
        if line:
            print(line, flush=True)


def show_interrupt(pending: PendingInterrupt) -> None:
    v = pending.value
    data = v.get("data", {})
    print("\n" + "=" * 78 + f"\n{v['title']}\n" + "=" * 78)
    if v.get("error"):
        print(f"!! {v['error']}\n")
    if v.get("artifact") == "plan":
        plan = data["plan"]
        print(plan["summary"] + "\n")
        for s in plan["user_stories"]:
            print(f"{s['id']}: {s['story']}")
            for ac in s["acceptance_criteria"]:
                print(f"    - {ac}")
        print("\nTasks (parallel layers):")
        tasks = {t["id"]: t for t in plan["tasks"]}
        for i, layer in enumerate(data["layers"], 1):
            for tid in layer:
                t = tasks[tid]
                deps = ", ".join(t["depends_on"]) or "-"
                print(f"  L{i} {tid} [{t['stack']}] {t['title']} (deps: {deps})")
                print(f"       files: {', '.join(t['target_files'])}")
    elif v.get("artifact") == "design":
        d = data["design"]
        print(f"stack={d['stack']} template={d['template_id']}\n")
        print(d["design_doc"])
    elif v.get("artifact") == "final":
        print(f"branch: {data.get('integration_branch')}")
        for tid, t in data.get("tasks", {}).items():
            print(f"  {tid}: {t['status']} (iterations {t['iterations']})")
        print(json.dumps(data.get("integration"), indent=2))
    else:
        for key in ("role", "task_id", "question", "reason", "options"):
            if data.get(key):
                print(f"{key}: {textwrap.shorten(str(data[key]), 600)}")
    print(f"\nallowed: {', '.join(v['allowed_actions'])}")


def edit_artifact(value: dict[str, Any]) -> dict[str, Any]:
    artifact = value["data"].get(value.get("artifact") or "", {})
    with tempfile.NamedTemporaryFile("w+", suffix=".json", delete=False) as f:
        json.dump(artifact, f, indent=2)
        path = f.name
    editor = os.environ.get("EDITOR")
    if editor:
        subprocess.run([editor, path], check=False)
    else:
        input(f"Edit {path} and press Enter when done... ")
    return dict(json.loads(Path(path).read_text()))


def ask(pending: PendingInterrupt, auto: bool) -> ResumePayload:
    allowed = pending.value["allowed_actions"]
    if auto:
        if "approve" in allowed:
            return ResumePayload.model_validate({"action": "approve"})
        return ResumePayload.model_validate({"action": "answer", "answer": AUTO_ANSWER})
    while True:
        choice = input("> [a]pprove / [r]eject / [e]dit / a[n]swer: ").strip().lower()
        try:
            if choice in ("a", "approve"):
                return ResumePayload.model_validate({"action": "approve"})
            if choice in ("r", "reject"):
                return ResumePayload.model_validate(
                    {"action": "reject", "feedback": input("feedback: ")}
                )
            if choice in ("e", "edit"):
                return ResumePayload.model_validate(
                    {"action": "edit", "artifact": edit_artifact(pending.value)}
                )
            if choice in ("n", "answer"):
                return ResumePayload.model_validate(
                    {"action": "answer", "answer": input("answer: ")}
                )
        except Exception as exc:
            print(f"invalid: {exc}")


# ------------------------------------------------------------------------------------ setup
@contextlib.asynccontextmanager
async def infrastructure(
    settings: Settings, memory: bool
) -> AsyncIterator[tuple[EventBus, BaseCheckpointSaver[Any], RunRepository | None]]:
    if memory:
        yield EventBus(InMemoryEventStore()), InMemorySaver(), None
        return
    engine = create_engine(settings.database_url)
    sessionmaker = create_sessionmaker(engine)
    try:
        async with postgres_checkpointer(settings.database_url) as saver:
            yield EventBus(PostgresEventStore(sessionmaker)), saver, RunRepository(sessionmaker)
    finally:
        await engine.dispose()


async def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("request", nargs="?", help="Feature request")
    parser.add_argument("--repo", default="local/devcrew-demo", help="owner/repo target")
    parser.add_argument("--resume", metavar="RUN_ID", help="Continue an existing run")
    parser.add_argument("--memory", action="store_true", help="No Postgres (not resumable)")
    parser.add_argument("--auto", action="store_true", help="Auto-approve and auto-answer")
    args = parser.parse_args()
    if not args.request and not args.resume:
        parser.error("give a request or --resume RUN_ID")

    env_file = DEVCREW_DIR / ".env"
    settings = Settings(_env_file=env_file if env_file.exists() else None)
    try:
        await preflight(settings, build_llm(settings).models.required_models())
    except PreflightError as exc:
        sys.exit(str(exc))

    async with infrastructure(settings, args.memory) as (bus, saver, runs):
        driver = RunDriver(build_graph(build_deps(settings, bus), saver), bus, runs)
        if args.resume:
            run_id = args.resume
        elif runs is not None:
            run_id = (
                await runs.create(request=args.request, repo_target=args.repo, create_repo=False)
            ).id
        else:
            run_id = new_run_id()
        print(f"run_id: {run_id}")
        printer = asyncio.create_task(print_events(bus, run_id))
        try:
            if args.resume:
                waiting = await driver.pending_interrupts(run_id)
                outcome = (
                    RunOutcome("interrupted", (await driver.state(run_id))["status"], waiting)
                    if waiting
                    else await driver.continue_run(run_id)
                )
            else:
                outcome = await driver.start(run_id, args.request, args.repo)
            while outcome.kind == "interrupted":
                await asyncio.sleep(0.2)  # let the event printer catch up
                pending = outcome.interrupts[0]
                show_interrupt(pending)
                try:
                    payload = ask(pending, args.auto)
                except (EOFError, KeyboardInterrupt):
                    print(f"\nPaused. Continue later with: run_local.py --resume {run_id}")
                    return
                outcome = await driver.resume(run_id, payload, pending.id)
        finally:
            await asyncio.sleep(0.2)
            printer.cancel()

        state = await driver.state(run_id)
        print(f"\nfinished: {outcome.status}" + (f" ({outcome.error})" if outcome.error else ""))
        if state.get("workspace"):
            print(f"workspace: {state['workspace']} (branch {state.get('integration_branch')})")


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
