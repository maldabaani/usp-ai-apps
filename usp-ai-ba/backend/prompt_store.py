"""Persisted per-kind overrides of the Ask Technical/Business system prompt
templates (Phase L-D). JSON-file-backed (sibling to config_store.py, which
rewrites backend/.env instead) since .env's quoting rules are awkward for
large multi-line prompt text.

Scope is deliberately narrow: only Ask Technical/Business, never StoryForge's
SDD-to-stories generation prompt (prompts/system_prompt.py) -- that one is
tightly coupled to the JSON schema the whole assessment pipeline depends on,
and letting it be edited from the UI risks silently breaking every downstream
node. Ask's prompts have no such schema coupling: they only ever produce
free-form streamed text.
"""
from __future__ import annotations

import json
import os
from typing import Literal

from config import settings

Kind = Literal["technical", "business"]

_cache: dict[str, str | None] | None = None


def _store_path() -> str:
    return os.path.join(settings.JOBS_DIR, "ask_prompts.json")


def _load() -> dict[str, str | None]:
    global _cache
    if _cache is not None:
        return _cache
    path = _store_path()
    if os.path.exists(path):
        with open(path) as f:
            _cache = json.load(f)
    else:
        _cache = {"technical": None, "business": None}
    return _cache


def _save() -> None:
    os.makedirs(settings.JOBS_DIR, exist_ok=True)
    with open(_store_path(), "w") as f:
        json.dump(_cache, f)


def validate_ask_prompt_template(template: str) -> None:
    """Raises ValueError with a UI-friendly message if template is unusable:
    either it's missing the required {context} placeholder, or it contains
    some other stray placeholder/brace that would break the real
    template.format(context=...) call this template is used with at request
    time (api/routers/ask.py's _stream_answer). A save-time substring search
    for "{context}" alone would not catch e.g. a stray {foo} elsewhere."""
    if "{context}" not in template:
        raise ValueError("Prompt template must include a {context} placeholder.")
    try:
        template.format(context="x")
    except (KeyError, IndexError, ValueError) as exc:
        raise ValueError(
            f"Prompt template has an invalid placeholder ({exc}). "
            "Curly braces other than {context} are not allowed."
        ) from exc


def get_custom_prompt(kind: Kind) -> str | None:
    return _load().get(kind)


def save_custom_prompt(kind: Kind, template: str | None) -> None:
    """template=None resets that kind back to its default (prompts/ask_prompts.py)."""
    if template is not None:
        validate_ask_prompt_template(template)
    store = _load()
    store[kind] = template
    _save()
