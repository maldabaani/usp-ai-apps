"""RulesCatalog.append_rule(): approved lessons land in the 900-999 id range, picked up by the
existing RULE_ID_RE/RULE_LINE_RE parsing with no new Reviewer-side code."""

from __future__ import annotations

from pathlib import Path

import pytest

from devcrew.tools.base import ToolError
from devcrew.tools.catalog import RulesCatalog


def rules_dir(tmp_path: Path, body: str = "# Python rules\n\n## Tests\n- **PY-030** pytest.\n") -> Path:
    d = tmp_path / "rules"
    d.mkdir()
    (d / "python.md").write_text(body, encoding="utf-8")
    return d


def test_append_rule_starts_at_901_and_adds_the_heading(tmp_path: Path) -> None:
    catalog = RulesCatalog(rules_dir(tmp_path))
    rule_id = catalog.append_rule("python", "Catch driver exceptions at the service boundary.")
    assert rule_id == "PY-901"
    text = catalog.read("python")
    assert "## Learned from experience" in text
    assert "- **PY-901** Catch driver exceptions at the service boundary." in text
    assert "PY-030" in text  # existing rules untouched


def test_append_rule_increments_past_existing_learned_rules(tmp_path: Path) -> None:
    body = (
        "# Python rules\n\n## Tests\n- **PY-030** pytest.\n\n"
        "## Learned from experience\n- **PY-901** Existing lesson.\n"
    )
    catalog = RulesCatalog(rules_dir(tmp_path, body))
    rule_id = catalog.append_rule("python", "Second lesson.")
    assert rule_id == "PY-902"
    assert catalog.rule_ids(["python"]) >= {"PY-030", "PY-901", "PY-902"}


def test_append_rule_rejects_an_unknown_stack(tmp_path: Path) -> None:
    catalog = RulesCatalog(rules_dir(tmp_path))
    with pytest.raises(ToolError):
        catalog.append_rule("cobol", "x")


def test_appended_rule_is_citable_immediately_without_a_restart(tmp_path: Path) -> None:
    """RulesCatalog.read() is uncached -- a second catalog instance over the same directory
    sees the appended rule right away, same as a live process would after a redeploy-free
    rules-file edit."""
    d = rules_dir(tmp_path)
    writer = RulesCatalog(d)
    rule_id = writer.append_rule("python", "A lesson.")
    reader = RulesCatalog(d)
    assert rule_id in reader.rule_ids(["python"])
    assert reader.rule_texts(["python"])[rule_id] == "A lesson."
