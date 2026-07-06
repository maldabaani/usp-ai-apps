"""Deterministic (LLM-free, zero-network) tally of a job's extraction
results -- how many files, how many succeeded/skipped/failed, how many
"rules" (extracted business-logic units) in total and per file.

Not a port: unlike most of codemind/, this has no Java ancestor in
com.jslogicextractor -- it's new, backing both scripts/count_extracted_logic.py
and qa.py's "generic" (stats) Ask mode. Consolidates what was previously three
separately-typed copies of "strip a markdown fence, then parse the model's
JSON content" (count_extracted_logic.py, api/routers/codemind_jobs.py's
_build_export_json, and the Angular job-detail viewer in TypeScript) into one
Python helper the first two now share.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from codemind import output
from codemind.agents.base import ExtractionResult


def parse_extracted_content(raw_content: str | None) -> dict | None:
    """Strips a leading/trailing markdown code fence (models sometimes wrap
    their JSON in ```json ... ``` despite being told not to) then parses the
    result as JSON. Returns None -- never raises -- for anything empty,
    unparseable, or not a JSON object, so callers can treat every failure mode
    uniformly as "unparseable"."""
    if not raw_content:
        return None
    cleaned = raw_content.strip()
    cleaned = cleaned.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        parsed = json.loads(cleaned)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


@dataclass(frozen=True)
class FileRuleCount:
    relative_path: str
    rule_count: int


@dataclass(frozen=True)
class ExtractionStats:
    total_files: int
    usable_files: list[FileRuleCount]
    skipped_or_failed_count: int
    unparseable: list[str]

    @property
    def usable_count(self) -> int:
        return len(self.usable_files)

    @property
    def total_rules(self) -> int:
        return sum(f.rule_count for f in self.usable_files)


def compute_stats(output_directory: Path) -> ExtractionStats:
    """Reads every result file a job wrote (via output.py's existing
    recent_files/read_output_file, the same path-traversal-safe pair
    api/routers/codemind_jobs.py's export route already uses -- not a fresh
    directory walk) and tallies them. Returns zeroed stats, not an error, for
    a directory that doesn't exist yet (a job still scanning/filtering has no
    results yet), matching recent_files' own not-created-yet handling."""
    usable_files: list[FileRuleCount] = []
    skipped_or_failed_count = 0
    unparseable: list[str] = []
    total_files = 0

    files = sorted(output.recent_files(output_directory, limit=10**9), key=lambda f: f.relative_path)
    for output_file in files:
        total_files += 1
        raw = output.read_output_file(output_directory, output_file.relative_path)
        if raw is None:
            unparseable.append(output_file.relative_path)
            continue
        try:
            data = json.loads(raw)
        except ValueError:
            unparseable.append(output_file.relative_path)
            continue

        result = ExtractionResult.from_dict(data)
        display_path = result.relative_path or output_file.relative_path

        if not result.success or result.skipped:
            skipped_or_failed_count += 1
            continue

        parsed = parse_extracted_content(result.content)
        if parsed is None:
            unparseable.append(display_path)
            continue

        rules = parsed.get("rules") or []
        usable_files.append(FileRuleCount(display_path, len(rules)))

    return ExtractionStats(
        total_files=total_files,
        usable_files=usable_files,
        skipped_or_failed_count=skipped_or_failed_count,
        unparseable=unparseable,
    )


def format_report(stats: ExtractionStats) -> str:
    """Human-readable report text -- total/per-file rule counts, skipped/
    failed/unparseable breakdown. Deliberately excludes any server filesystem
    path (unlike the CLI script, which prints the output directory itself,
    separately) since this is also used to answer a browser-facing Ask
    question in qa.py's generic mode."""
    lines = [
        f"Files with usable extraction results: {stats.usable_count}",
        f"Files skipped/failed (no logic extracted): {stats.skipped_or_failed_count}",
        f"Files whose content wasn't valid JSON (excluded from the count): {len(stats.unparseable)}",
        f"Total extracted rules across all files: {stats.total_rules}",
        "",
        "Per-file rule counts:",
    ]
    for file in sorted(stats.usable_files, key=lambda f: f.rule_count, reverse=True):
        lines.append(f"  {file.rule_count:>4}  {file.relative_path}")
    if stats.unparseable:
        lines.append("")
        lines.append("Files excluded (content wasn't valid JSON):")
        for relative_path in stats.unparseable:
            lines.append(f"  {relative_path}")
    return "\n".join(lines)
