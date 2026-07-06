"""Covers codemind/extraction_stats.py -- the deterministic, LLM-free tally
shared by scripts/count_extracted_logic.py and qa.py's generic Ask mode."""
import json

from codemind import output
from codemind.agents.base import ExtractionResult
from codemind.extraction_stats import (
    ExtractionStats,
    FileRuleCount,
    compute_stats,
    format_report,
    parse_extracted_content,
)


def _write(output_directory, relative_path: str, result: ExtractionResult) -> None:
    output.write_result(output_directory, relative_path, result.to_dict())


def test_parse_extracted_content_strips_json_fence():
    raw = '```json\n{"rules": [{"name": "a"}]}\n```'
    assert parse_extracted_content(raw) == {"rules": [{"name": "a"}]}


def test_parse_extracted_content_strips_bare_fence():
    raw = '```\n{"rules": []}\n```'
    assert parse_extracted_content(raw) == {"rules": []}


def test_parse_extracted_content_handles_unfenced_json():
    assert parse_extracted_content('{"rules": [{"name": "a"}, {"name": "b"}]}') == {
        "rules": [{"name": "a"}, {"name": "b"}]
    }


def test_parse_extracted_content_returns_none_for_invalid_json():
    assert parse_extracted_content("not json at all") is None


def test_parse_extracted_content_returns_none_for_empty_or_missing():
    assert parse_extracted_content("") is None
    assert parse_extracted_content(None) is None


def test_parse_extracted_content_returns_none_for_non_object_json():
    assert parse_extracted_content("[1, 2, 3]") is None
    assert parse_extracted_content("42") is None


def test_compute_stats_on_nonexistent_directory_returns_zeroed_stats(tmp_path):
    stats = compute_stats(tmp_path / "not-created-yet")

    assert stats == ExtractionStats(total_files=0, usable_files=[], skipped_or_failed_count=0, unparseable=[])
    assert stats.usable_count == 0
    assert stats.total_rules == 0


def test_compute_stats_tallies_usable_skipped_failed_and_unparseable(tmp_path):
    _write(
        tmp_path,
        "auth.js",
        ExtractionResult("auth.js", "test-agent", True, False, '{"rules": [{"name": "a"}, {"name": "b"}]}', None, 1, None, None),
    )
    _write(
        tmp_path,
        "payments.js",
        ExtractionResult(
            "payments.js", "test-agent", True, False, '```json\n{"rules": [{"name": "c"}]}\n```', None, 1, None, None
        ),
    )
    _write(
        tmp_path,
        "broken.js",
        ExtractionResult("broken.js", "test-agent", False, False, None, "boom", 1, None, None),
    )
    _write(
        tmp_path,
        "skipped.js",
        ExtractionResult("skipped.js", "test-agent", True, True, None, "non-substantive", 0, None, None),
    )
    _write(
        tmp_path,
        "garbled.js",
        ExtractionResult("garbled.js", "test-agent", True, False, "not valid json", None, 1, None, None),
    )
    output.write_summary(tmp_path, {"jobId": "x"})  # must be excluded from every count

    stats = compute_stats(tmp_path)

    assert stats.total_files == 5
    assert stats.skipped_or_failed_count == 2  # broken.js + skipped.js
    assert set(stats.unparseable) == {"garbled.js"}
    assert stats.usable_count == 2
    assert stats.total_rules == 3
    assert FileRuleCount("auth.js", 2) in stats.usable_files
    assert FileRuleCount("payments.js", 1) in stats.usable_files


def test_compute_stats_counts_unparseable_outer_json(tmp_path):
    (tmp_path / "corrupt.json").write_text("{not valid json", encoding="utf-8")

    stats = compute_stats(tmp_path)

    assert stats.total_files == 1
    assert stats.unparseable == ["corrupt.json"]
    assert stats.usable_count == 0


def test_format_report_lists_files_sorted_by_rule_count_descending():
    stats = ExtractionStats(
        total_files=2,
        usable_files=[FileRuleCount("small.js", 1), FileRuleCount("big.js", 5)],
        skipped_or_failed_count=0,
        unparseable=[],
    )

    report = format_report(stats)

    assert "Total extracted rules across all files: 6" in report
    assert report.index("big.js") < report.index("small.js")
    assert "Files excluded" not in report


def test_format_report_includes_unparseable_section_only_when_present():
    stats = ExtractionStats(total_files=1, usable_files=[], skipped_or_failed_count=0, unparseable=["weird.js"])

    report = format_report(stats)

    assert "Files excluded (content wasn't valid JSON):" in report
    assert "weird.js" in report


def test_compute_stats_ignores_comprehensive_summary_file(tmp_path):
    _write(tmp_path, "auth.js", ExtractionResult("auth.js", "test-agent", True, False, '{"rules": []}', None, 1, None, None))
    output.write_comprehensive_summary(tmp_path, {"summary": "overview text"})

    stats = compute_stats(tmp_path)

    assert stats.total_files == 1
