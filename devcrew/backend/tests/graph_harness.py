"""Shared fixtures for graph tests: default brain, deps, driver."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver

from app.config import BACKEND_DIR, DEVCREW_DIR, Settings
from app.events.bus import EventBus
from app.events.store import InMemoryEventStore
from app.events.types import Event
from app.github.delivery import GitHubDelivery
from app.graph.backbone import build_graph
from app.graph.interrupts import ResumePayload
from app.graph.runner import RunDriver, RunOutcome
from app.graph.runtime import GraphDeps
from app.prompts import PromptLibrary
from app.rag.embeddings import Embedder
from app.rag.service import RagService
from app.sandbox.service import Sandbox
from app.tools.catalog import RulesCatalog, TemplatesCatalog
from tests.fakes import (
    Brain,
    Call,
    FakeChroma,
    FakeRunner,
    HashEmbeddingModel,
    final,
    gateway,
    tool_call,
    tool_results,
)

PLAN: dict[str, Any] = {
    "summary": "TODO API",
    "user_stories": [
        {"id": "US1", "story": "As a user I want todos", "acceptance_criteria": ["CRUD works"]}
    ],
    "tasks": [
        {
            "id": "T1",
            "title": "Todo model",
            "description": "Pydantic schemas",
            "target_files": ["app/schemas/todo.py"],
            "depends_on": [],
            "stack": "python",
            "story_ids": ["US1"],
        },
        {
            "id": "T2",
            "title": "Todo router",
            "description": "CRUD endpoints",
            "target_files": ["app/routers/todos.py"],
            "depends_on": ["T1"],
            "stack": "python",
            "story_ids": ["US1"],
        },
    ],
}

DESIGN: dict[str, Any] = {
    "stack": "python",
    "template_id": "python-fastapi",
    "project_structure": ["app/main.py", "app/schemas/todo.py", "app/routers/todos.py"],
    "modules": [
        {
            "name": "todos",
            "path": "app/routers/todos.py",
            "responsibility": "CRUD endpoints",
            "interface": "GET/POST /todos",
        }
    ],
    "key_decisions": ["in-memory store"],
    "design_doc": "# Design\nTODO API",
}

ESCALATE = {"action": "escalate", "reason": "needs a human", "question_for_human": "What now?"}

APPROVE = ResumePayload(action="approve")


def current_task_id(call: Call) -> str:
    return str(call.messages[1].content).split("id: ", 1)[1].split("\n", 1)[0]


def default_developer(call: Call) -> AIMessage:
    if call.fresh or not tool_results(call):
        tid = current_task_id(call)
        path = {"T1": "app/schemas/todo.py", "T2": "app/routers/todos.py"}.get(
            tid, f"app/{tid.lower()}.py"
        )
        attempt = sum(1 for m in call.messages if "Feedback to address" in str(m.content))
        return tool_call("write_file", path=path, content=f"# {tid} attempt {attempt}\nX = 1\n")
    return final("Implemented the task.")


def default_qa(call: Call) -> AIMessage:
    if not tool_results(call):
        tid = current_task_id(call)
        return tool_call(
            "write_file",
            path=f"tests/test_{tid.lower()}.py",
            content="def test_x():\n    assert True\n",
        )
    return final("Tests cover the happy path.")


def default_architect(call: Call) -> AIMessage:
    if not tool_results(call):
        return tool_call("list_templates")
    return final(DESIGN)


def default_brain() -> Brain:
    return Brain(
        responders={
            "planner": lambda c: final(PLAN),
            "architect": default_architect,
            "developer": default_developer,
            "reviewer": lambda c: final({"decision": "approve", "summary": "ok", "issues": []}),
            "qa": default_qa,
            "coordinator": lambda c: final(ESCALATE),
        }
    )


def make_templates(root: Path) -> Path:
    tpl = root / "templates" / "python"
    (tpl / "app").mkdir(parents=True)
    (tpl / "template.yaml").write_text(
        "id: python-fastapi\nstack: python\ndescription: d\ninstall_cmd: pip install -e .\n"
        "build_cmd: b\ntest_cmd: pytest -q\n"
    )
    (tpl / "app" / "main.py").write_text("app = None\n")
    (tpl / "pyproject.toml").write_text("[project]\nname = 'app'\n")
    ng = root / "templates" / "angular"
    (ng / "src").mkdir(parents=True)
    (ng / "template.yaml").write_text(
        "id: angular-standalone\nstack: angular\ndescription: d\ninstall_cmd: npm install\n"
        "build_cmd: b\ntest_cmd: npx ng test --watch=false --browsers=ChromeHeadless\n"
    )
    (ng / "package.json").write_text("{}\n")
    (ng / "src" / "main.ts").write_text("// app\n")
    return root / "templates"


@dataclass
class Harness:
    brain: Brain
    deps: GraphDeps
    driver: RunDriver
    store: InMemoryEventStore
    checkpointer: BaseCheckpointSaver[Any]

    async def events(self, run_id: str) -> list[Event]:
        return await self.store.list_after(run_id, 0, 10_000)

    def rebuild(self) -> Harness:
        """Simulate a backend restart: new graph + driver over the same checkpointer."""
        graph = build_graph(self.deps, self.checkpointer)
        return Harness(
            self.brain, self.deps, RunDriver(graph, self.deps.events), self.store, self.checkpointer
        )

    async def approve_until_done(self, outcome: RunOutcome, run_id: str) -> RunOutcome:
        while outcome.kind == "interrupted":
            outcome = await self.driver.resume(run_id, APPROVE)
        return outcome


def make_harness(
    tmp_path: Path,
    brain: Brain | None = None,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
    runner: FakeRunner | None = None,
    chroma: FakeChroma | None = None,
    github: GitHubDelivery | None = None,
    **settings: Any,
) -> Harness:
    brain = brain or default_brain()
    settings.setdefault("gates_enabled", False)  # gate tests turn them on explicitly
    settings.setdefault("watch_prs", False)  # PR follow-up tests turn it on explicitly
    cfg = Settings(
        _env_file=None,
        workspaces_dir=tmp_path / "ws",
        templates_dir=make_templates(tmp_path),
        rules_dir=DEVCREW_DIR / "rules",
        prompts_dir=BACKEND_DIR / "prompts",
        **settings,
    )
    store = InMemoryEventStore()
    deps = GraphDeps(
        settings=cfg,
        llm=gateway(brain),
        events=EventBus(store),
        prompts=PromptLibrary(cfg.prompts_dir),
        rules=RulesCatalog(cfg.rules_dir),
        templates=TemplatesCatalog(cfg.templates_dir),
        sandbox=Sandbox(runner) if runner is not None else None,
        rag=(
            RagService(lambda: chroma, Embedder(HashEmbeddingModel(), model_name="nomic"), cfg)
            if chroma is not None
            else None
        ),
        github=github,
    )
    saver = checkpointer or InMemorySaver()
    graph = build_graph(deps, saver)
    return Harness(brain, deps, RunDriver(graph, deps.events), store, saver)
