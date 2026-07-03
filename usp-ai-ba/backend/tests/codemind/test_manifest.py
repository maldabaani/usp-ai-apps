"""Ported from com.jslogicextractor.incremental.ManifestServiceTest."""
from codemind import manifest
from codemind.models import SourceFile


def test_returns_empty_when_no_manifest_exists(tmp_path):
    assert manifest.load(tmp_path, tmp_path / "repo") is None


def test_saves_and_loads_manifest_round_trip(tmp_path):
    repo_root = tmp_path / "repo"
    hashes = {"src/a.js": "hash1", "src/b.ts": "hash2"}
    output_dir = tmp_path / "output/job-123"

    manifest.save(tmp_path, repo_root, manifest.Manifest(output_dir, hashes))
    loaded = manifest.load(tmp_path, repo_root)

    assert loaded is not None
    assert loaded.output_directory == output_dir.absolute()
    assert loaded.file_hashes == hashes


def test_different_repo_roots_produce_different_manifest_files(tmp_path):
    repo_a = tmp_path / "repoA"
    repo_b = tmp_path / "repoB"
    hashes_a = {"a.js": "aaa"}
    hashes_b = {"b.js": "bbb"}

    manifest.save(tmp_path, repo_a, manifest.Manifest(tmp_path / "outA", hashes_a))
    manifest.save(tmp_path, repo_b, manifest.Manifest(tmp_path / "outB", hashes_b))

    assert manifest.load(tmp_path, repo_a).file_hashes == hashes_a
    assert manifest.load(tmp_path, repo_b).file_hashes == hashes_b


def test_compute_hashes_deduplicates_chunked_files(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True)
    file = repo_root / "big.js"
    file.write_text("const x = 1;")

    # Simulate two chunks from the same original file
    chunk1 = SourceFile(file, "big.js/part-0001.js", "const x", 7)
    chunk2 = SourceFile(file, "big.js/part-0002.js", " = 1;", 5)

    hashes = manifest.compute_hashes(repo_root, [chunk1, chunk2])

    # Should produce one entry keyed by the original file path, not by chunk path
    assert len(hashes) == 1
    assert "big.js" in hashes


def test_compute_hashes_produces_consistent_hash_for_same_content(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True)
    file = repo_root / "a.js"
    file.write_text("const a = 1;")
    source_file = SourceFile(file, "a.js", "const a = 1;", 12)

    first = manifest.compute_hashes(repo_root, [source_file])
    second = manifest.compute_hashes(repo_root, [source_file])

    assert first == second
    assert len(first["a.js"]) == 64  # SHA-256 hex = 64 chars


def test_compute_hashes_produces_different_hash_after_content_change(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True)
    file = repo_root / "a.js"
    file.write_text("const a = 1;")
    before = SourceFile(file, "a.js", "const a = 1;", 12)
    hash_before = manifest.compute_hashes(repo_root, [before])

    file.write_text("const a = 2;")
    after = SourceFile(file, "a.js", "const a = 2;", 12)
    hash_after = manifest.compute_hashes(repo_root, [after])

    assert hash_before["a.js"] != hash_after["a.js"]


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
