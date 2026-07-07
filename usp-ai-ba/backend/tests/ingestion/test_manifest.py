"""Covers ingestion/manifest.py, moved from codemind/manifest.py (originally
ported from com.jslogicextractor.incremental.ManifestService), adapted for
the unified ingestion pipeline: dropped the output_directory field (there's
no per-job flat-JSON output directory to track anymore in the ChromaDB-backed
model) and simplified compute_hashes(repo, [SourceFile]) to a plain per-file
compute_hash(absolute_path), since callers hash files directly rather than
through CodeMind's chunked-SourceFile abstraction. Shared by both of
ingest_code.py's tiers (mechanical chunking and LLM-summary enrichment), each
with its own manifest namespace -- see this module's own docstring.
"""
from ingestion import manifest


def test_returns_empty_when_no_manifest_exists(tmp_path):
    assert manifest.load(tmp_path, tmp_path / "repo") is None


def test_saves_and_loads_manifest_round_trip(tmp_path):
    repo_root = tmp_path / "repo"
    hashes = {"src/a.js": "hash1", "src/b.ts": "hash2"}

    manifest.save(tmp_path, repo_root, hashes)
    loaded = manifest.load(tmp_path, repo_root)

    assert loaded == hashes


def test_different_repo_roots_produce_different_manifest_files(tmp_path):
    repo_a = tmp_path / "repoA"
    repo_b = tmp_path / "repoB"
    hashes_a = {"a.js": "aaa"}
    hashes_b = {"b.js": "bbb"}

    manifest.save(tmp_path, repo_a, hashes_a)
    manifest.save(tmp_path, repo_b, hashes_b)

    assert manifest.load(tmp_path, repo_a) == hashes_a
    assert manifest.load(tmp_path, repo_b) == hashes_b


def test_compute_hash_produces_consistent_hash_for_same_content(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True)
    file = repo_root / "a.js"
    file.write_text("const a = 1;")

    first = manifest.compute_hash(file)
    second = manifest.compute_hash(file)

    assert first == second
    assert len(first) == 64  # SHA-256 hex = 64 chars


def test_compute_hash_produces_different_hash_after_content_change(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True)
    file = repo_root / "a.js"

    file.write_text("const a = 1;")
    hash_before = manifest.compute_hash(file)

    file.write_text("const a = 2;")
    hash_after = manifest.compute_hash(file)

    assert hash_before != hash_after


def test_compute_hash_returns_none_for_unreadable_file(tmp_path):
    assert manifest.compute_hash(tmp_path / "does-not-exist.js") is None


def test_diff_detects_added_modified_and_deleted_files():
    previous = {
        "src/unchanged.js": "hash-unchanged",
        "src/modified.js": "hash-old",
        "src/deleted.js": "hash-deleted",
    }
    current = {
        "src/unchanged.js": "hash-unchanged",
        "src/modified.js": "hash-new",
        "src/added.js": "hash-added",
    }

    changes = manifest.diff(previous, current)

    assert changes.added == ["src/added.js"]
    assert changes.modified == ["src/modified.js"]
    assert changes.deleted == ["src/deleted.js"]


def test_diff_changed_or_added_returns_both_added_and_modified():
    previous = {"old.js": "hash-old"}
    current = {"old.js": "hash-new", "new.js": "hash-new2"}

    changes = manifest.diff(previous, current)

    assert set(changes.changed_or_added()) == {"old.js", "new.js"}


def test_diff_returns_no_changes_for_identical_manifests():
    hashes = {"a.js": "h1", "b.js": "h2"}

    changes = manifest.diff(hashes, hashes)

    assert changes.added == []
    assert changes.modified == []
    assert changes.deleted == []
