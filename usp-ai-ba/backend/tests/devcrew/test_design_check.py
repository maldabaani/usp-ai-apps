"""The design's example code must parse and define what it uses (Mac run: T2 failed review
three times against a router that used an undefined BookmarkService)."""

from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.messages import AIMessage
from pydantic import ValidationError

from devcrew.graph.design_check import python_snippet_problems
from devcrew.graph.state import Design
from tests.devcrew.api_harness import api
from tests.devcrew.fakes import Call, final, tool_call, tool_results
from tests.devcrew.graph_harness import DESIGN

REQUEST = {"request": "Bookmarks API", "repo_target": "me/bookmarks", "create_repo": False}

BROKEN = """
```python
from pydantic import BaseModel

class Bookmark(BaseModel):
    title: str
```

```python
def create_bookmark(bookmark: Bookmark) -> Bookmark:
    # Create and return a bookmark
```

```python
from fastapi import APIRouter, Depends

router = APIRouter()

@router.post("/bookmarks", response_model=Bookmark)
def create(bookmark: Bookmark, service: BookmarkService = Depends()) -> Bookmark:
    return service.create_bookmark(bookmark)
```
"""

FIXED = BROKEN.replace("# Create and return a bookmark", "...").replace(
    "service: BookmarkService = Depends()", "service: list[str] = Depends()"
)


def test_broken_examples_are_reported() -> None:
    problems = python_snippet_problems(BROKEN)
    assert len(problems) == 2
    assert problems[0].startswith("python block 2: syntax error")
    assert "BookmarkService used but never defined" in problems[1]


def test_consistent_examples_pass() -> None:
    # names defined in another block count (contracts are split into several blocks)
    assert python_snippet_problems(FIXED) == []
    assert python_snippet_problems("no code, only prose") == []


RULE_BREAKING = """
```python
bookmark_store: dict[int, dict[str, str]] = {}
next_id = 1

def create(data: dict[str, str]) -> dict[str, str]:
    global next_id
    next_id += 1
    return dict(data.dict())
```
"""

INSTANCE_STATE = """
```python
from fastapi import APIRouter

router = APIRouter()

class BookmarkService:
    def __init__(self) -> None:
        self._items: dict[int, str] = {}

_service = BookmarkService()

def get_bookmark_service() -> BookmarkService:
    return _service

def check(response: object) -> None:
    assert response.json()
```
"""


def test_stack_rule_violations_in_design_code() -> None:
    # the Mac run's design: module-level store, global counter, Pydantic v1 .dict()
    problems = " | ".join(python_snippet_problems(RULE_BREAKING))
    assert "module-level mutable state (PY-005)" in problems
    assert "`global next_id` (PY-005)" in problems
    assert "Pydantic v1 `.dict()`" in problems
    # state in a class instance behind Depends is the allowed pattern; response.json() is fine
    assert python_snippet_problems(INSTANCE_STATE) == []


def design(doc: str) -> dict[str, object]:
    return {
        "stack": "python",
        "template_id": "python-fastapi",
        "project_structure": ["app/"],
        "modules": [{"name": "m", "path": "app/", "responsibility": "r", "interface": "i"}],
        "design_doc": doc,
    }


def test_only_the_architect_output_is_checked() -> None:
    # stored designs load unchanged; the check runs only with check_code (the Architect's call)
    Design.model_validate(design(BROKEN))
    with pytest.raises(ValidationError, match="BookmarkService"):
        Design.model_validate(design(BROKEN), context={"check_code": True})
    Design.model_validate(design(FIXED), context={"check_code": True})


async def test_architect_fixes_its_code_or_the_human_is_told(tmp_path: Path) -> None:
    async with api(tmp_path) as a:
        answers = iter([BROKEN, FIXED])

        def architect(call: Call) -> AIMessage:
            if not tool_results(call):
                return tool_call("list_templates")
            return final({**DESIGN, "design_doc": next(answers, BROKEN)})

        a.harness.brain.responders["architect"] = architect
        resp = await a.client.post("/runs", json=REQUEST)
        run_id = str(resp.json()["id"])
        await a.settle(run_id)
        run = await a.approve(run_id)
        # the validation error went back to the model, which fixed the example code
        assert run["design"]["design_doc"] == FIXED
        assert not (run["design"].get("plan_assessment") or {}).get("concerns")


async def test_unfixable_design_code_becomes_a_concern(tmp_path: Path) -> None:
    async with api(tmp_path) as a:

        def architect(call: Call) -> AIMessage:
            if not tool_results(call):
                return tool_call("list_templates")
            return final({**DESIGN, "design_doc": BROKEN})

        a.harness.brain.responders["architect"] = architect
        resp = await a.client.post("/runs", json=REQUEST)
        run_id = str(resp.json()["id"])
        await a.settle(run_id)
        run = await a.approve(run_id)
        assert run["status"] == "awaiting_design_approval"
        concerns = run["design"]["plan_assessment"]["concerns"]
        assert any("BookmarkService" in c for c in concerns)
