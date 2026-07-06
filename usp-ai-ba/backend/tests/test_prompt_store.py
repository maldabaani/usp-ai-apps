"""Covers prompt_store.py's persisted Ask Technical/Business prompt
overrides and its save-time validation (Phase L-D) -- the validation must
actually attempt template.format(context=...), not just substring-search
for "{context}", since a stray placeholder elsewhere would otherwise only
break at request time (see validate_ask_prompt_template's docstring)."""
from __future__ import annotations

import pytest

import prompt_store
from config import settings


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "JOBS_DIR", str(tmp_path / "jobs"))
    prompt_store._cache = None
    yield
    prompt_store._cache = None


def test_valid_template_is_accepted_and_round_trips():
    prompt_store.save_custom_prompt("technical", "Custom prompt.\nContext:\n{context}\n")

    assert prompt_store.get_custom_prompt("technical") == "Custom prompt.\nContext:\n{context}\n"
    assert prompt_store.get_custom_prompt("business") is None


def test_missing_context_placeholder_is_rejected():
    with pytest.raises(ValueError, match=r"\{context\}"):
        prompt_store.save_custom_prompt("technical", "No placeholder here.")

    assert prompt_store.get_custom_prompt("technical") is None


def test_stray_placeholder_is_rejected_naming_it():
    with pytest.raises(ValueError, match="foo"):
        prompt_store.save_custom_prompt("business", "Context: {context}. Also {foo}.")

    assert prompt_store.get_custom_prompt("business") is None


def test_stray_unmatched_brace_is_rejected():
    with pytest.raises(ValueError):
        prompt_store.save_custom_prompt("technical", "Context: {context}. A stray {")


def test_saving_none_resets_to_default():
    prompt_store.save_custom_prompt("technical", "Custom.\n{context}\n")
    assert prompt_store.get_custom_prompt("technical") is not None

    prompt_store.save_custom_prompt("technical", None)

    assert prompt_store.get_custom_prompt("technical") is None


def test_validate_ask_prompt_template_directly():
    prompt_store.validate_ask_prompt_template("Fine: {context}")

    with pytest.raises(ValueError):
        prompt_store.validate_ask_prompt_template("No placeholder")
