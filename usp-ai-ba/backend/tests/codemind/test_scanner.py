"""Ported from com.jslogicextractor.scanner.RepositoryScannerServiceTest."""
import pytest

from codemind import scanner

_INCLUDED_EXTENSIONS = {
    ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx",
    ".py", ".pyw", ".java", ".kt", ".kts",
    ".go", ".cs", ".rb", ".rs", ".php",
}
_EXCLUDED_DIRECTORY_NAMES = {
    "node_modules", ".git", "dist", "build", "coverage",
    "out", ".next", ".turbo", "vendor",
    "__pycache__", "target", ".venv", "venv",
    "bin", "obj", ".gradle", ".mypy_cache", ".pytest_cache",
}


def _write(path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def test_scans_included_extensions_and_skips_excluded_directories(tmp_path):
    _write(tmp_path / "src/index.js", "console.log('hi');")
    _write(tmp_path / "src/app.tsx", "export const App = () => null;")
    _write(tmp_path / "README.md", "# docs")
    _write(tmp_path / "node_modules/lib/index.js", "module.exports = {};")
    _write(tmp_path / "dist/bundle.js", "/* generated */")

    files = scanner.scan(
        tmp_path,
        included_extensions=_INCLUDED_EXTENSIONS,
        excluded_directory_names=_EXCLUDED_DIRECTORY_NAMES,
        max_file_size_bytes=300_000,
        chunking_enabled=True,
        max_lines_per_chunk=1800,
    )

    assert {f.relative_path for f in files} == {"src/index.js", "src/app.tsx"}


def test_skips_files_larger_than_max_size_when_chunking_disabled(tmp_path):
    _write(tmp_path / "big.js", "x" * 100)

    files = scanner.scan(
        tmp_path,
        included_extensions=_INCLUDED_EXTENSIONS,
        excluded_directory_names=_EXCLUDED_DIRECTORY_NAMES,
        max_file_size_bytes=10,
        chunking_enabled=False,
        max_lines_per_chunk=0,
    )

    assert files == []


def test_splits_files_larger_than_max_size_into_chunks_when_chunking_enabled(tmp_path):
    _write(tmp_path / "big.js", "const a = 1;\nconst b = 2;\nconst c = 3;")

    files = scanner.scan(
        tmp_path,
        included_extensions=_INCLUDED_EXTENSIONS,
        excluded_directory_names=_EXCLUDED_DIRECTORY_NAMES,
        max_file_size_bytes=10,
        chunking_enabled=True,
        max_lines_per_chunk=1,
    )

    assert [f.relative_path for f in files] == [
        "big.js/part-0001.js",
        "big.js/part-0002.js",
        "big.js/part-0003.js",
    ]


def test_rejects_non_directory_root(tmp_path):
    file = tmp_path / "not-a-dir.txt"
    _write(file, "x")

    with pytest.raises(ValueError):
        scanner.scan(
            file,
            included_extensions=_INCLUDED_EXTENSIONS,
            excluded_directory_names=_EXCLUDED_DIRECTORY_NAMES,
            max_file_size_bytes=300_000,
            chunking_enabled=True,
            max_lines_per_chunk=1800,
        )


def test_scan_file_returns_single_source_file_with_included_extension(tmp_path):
    file = tmp_path / "dropped.js"
    _write(file, "const x = 1;")

    files = scanner.scan_file(
        file,
        included_extensions=_INCLUDED_EXTENSIONS,
        max_file_size_bytes=300_000,
        chunking_enabled=True,
        max_lines_per_chunk=1800,
    )

    assert [f.relative_path for f in files] == ["dropped.js"]


def test_scan_file_skips_files_with_excluded_extension(tmp_path):
    file = tmp_path / "notes.txt"
    _write(file, "not js")

    files = scanner.scan_file(
        file,
        included_extensions=_INCLUDED_EXTENSIONS,
        max_file_size_bytes=300_000,
        chunking_enabled=True,
        max_lines_per_chunk=1800,
    )

    assert files == []


def test_scan_file_rejects_non_file_path(tmp_path):
    directory = tmp_path / "a-directory"
    directory.mkdir(parents=True)

    with pytest.raises(ValueError):
        scanner.scan_file(
            directory,
            included_extensions=_INCLUDED_EXTENSIONS,
            max_file_size_bytes=300_000,
            chunking_enabled=True,
            max_lines_per_chunk=1800,
        )
