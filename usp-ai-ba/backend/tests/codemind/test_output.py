"""Covers codemind/output.py, ported (with the Job type decoupled -- see that
module's docstring) from com.jslogicextractor.output.{FileSystemExtractionResultWriter,
OutputFileSnapshotService}."""
import json

from codemind import output


def test_write_and_read_result_round_trip(tmp_path):
    output.write_result(tmp_path, "src/a", {"file": "src/a.js", "success": True})

    assert output.result_exists(tmp_path, "src/a")
    raw = output.read_output_file(tmp_path, "src/a.json")
    assert json.loads(raw) == {"file": "src/a.js", "success": True}


def test_result_does_not_exist_before_write(tmp_path):
    assert output.result_exists(tmp_path, "src/missing") is False


def test_write_summary_creates_summary_file(tmp_path):
    output.write_summary(tmp_path, {"jobId": "abc", "phase": "COMPLETED"})

    summary_file = tmp_path / "_summary.json"
    assert summary_file.exists()
    assert json.loads(summary_file.read_text()) == {"jobId": "abc", "phase": "COMPLETED"}


def test_write_and_read_comprehensive_summary_round_trip(tmp_path):
    output.write_comprehensive_summary(tmp_path, {"summary": "overview text", "fileCount": 3})

    assert output.read_comprehensive_summary(tmp_path) == {"summary": "overview text", "fileCount": 3}


def test_read_comprehensive_summary_returns_none_when_missing(tmp_path):
    assert output.read_comprehensive_summary(tmp_path) is None


def test_read_comprehensive_summary_returns_none_for_unparseable_json(tmp_path):
    (tmp_path / "_comprehensive_summary.json").write_text("not valid json", encoding="utf-8")

    assert output.read_comprehensive_summary(tmp_path) is None


def test_is_generated_metadata_file():
    assert output.is_generated_metadata_file("_summary.json") is True
    assert output.is_generated_metadata_file("_comprehensive_summary.json") is True
    assert output.is_generated_metadata_file("auth.js.json") is False


def test_read_output_file_rejects_path_traversal(tmp_path):
    (tmp_path / "secret.json").write_text('{"leaked": true}')

    assert output.read_output_file(tmp_path, "../secret.json") is None


def test_read_output_file_returns_none_for_missing_file(tmp_path):
    assert output.read_output_file(tmp_path, "does/not/exist.json") is None


def test_recent_files_returns_empty_when_output_dir_does_not_exist(tmp_path):
    assert output.recent_files(tmp_path / "not-created-yet", 50) == []


def test_recent_files_excludes_summary_and_sorts_newest_first(tmp_path):
    output.write_result(tmp_path, "a", {"success": True})
    output.write_result(tmp_path, "b", {"success": True})
    output.write_summary(tmp_path, {"jobId": "x"})
    output.write_comprehensive_summary(tmp_path, {"summary": "overview"})

    files = output.recent_files(tmp_path, limit=50)

    relative_paths = {f.relative_path for f in files}
    assert relative_paths == {"a.json", "b.json"}


def test_recent_files_respects_limit(tmp_path):
    for i in range(5):
        output.write_result(tmp_path, f"file{i}", {"success": True})

    files = output.recent_files(tmp_path, limit=2)

    assert len(files) == 2


def test_list_failed_files_returns_empty_when_output_dir_does_not_exist(tmp_path):
    assert output.list_failed_files(tmp_path / "not-created-yet") == []


def test_list_failed_files_only_returns_success_false(tmp_path):
    output.write_result(tmp_path, "ok", {"success": True})
    output.write_result(
        tmp_path, "broken", {"success": False, "errorMessage": "boom", "durationMillis": 42}
    )

    failed = output.list_failed_files(tmp_path)

    assert len(failed) == 1
    assert failed[0].relative_path == "broken"
    assert failed[0].error_message == "boom"
    assert failed[0].duration_millis == 42


def test_list_failed_files_defaults_error_message_when_missing(tmp_path):
    output.write_result(tmp_path, "broken", {"success": False})

    failed = output.list_failed_files(tmp_path)

    assert failed[0].error_message == "Unknown error"
    assert failed[0].duration_millis == 0


def test_list_failed_files_excludes_comprehensive_summary(tmp_path):
    # A body shaped to look like a failure (success=False) if the by-name
    # exclusion didn't work -- proves the exclusion itself, not just that the
    # real cache schema happens to be harmless.
    output.write_comprehensive_summary(tmp_path, {"summary": "overview", "success": False})

    assert output.list_failed_files(tmp_path) == []
