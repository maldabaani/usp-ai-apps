"""Ported from com.jslogicextractor.filter.NonSubstantiveFileFilterTest."""
from pathlib import Path

from codemind import filter as nonsubstantive_filter
from codemind.models import SourceFile


def _file(relative_path: str, content: str) -> SourceFile:
    return SourceFile(Path(relative_path), relative_path, content, len(content))


def test_skips_type_declaration_files():
    reason = nonsubstantive_filter.skip_reason(_file("src/types/index.d.ts", "export type Foo = string;"))
    assert reason is not None
    assert "type-declaration" in reason


def test_skips_test_filename_suffixes():
    assert nonsubstantive_filter.skip_reason(_file("src/Widget.test.ts", "test('x', () => {});")) is not None
    assert nonsubstantive_filter.skip_reason(_file("src/Widget.spec.jsx", "describe('x', () => {});")) is not None


def test_skips_files_under_test_directories():
    assert nonsubstantive_filter.skip_reason(_file("__tests__/widget.js", "test('x', () => {});")) is not None
    assert nonsubstantive_filter.skip_reason(_file("src/tests/helpers.js", "module.exports = {};")) is not None
    assert nonsubstantive_filter.skip_reason(_file("src/test/helpers.js", "module.exports = {};")) is not None


def test_skips_barrel_files_containing_only_imports_and_re_exports():
    content = (
        "import './polyfills';\n"
        "export * from './widget';\n"
        "export { Button } from './button';\n"
        "export type { Props } from './props';\n"
    )
    reason = nonsubstantive_filter.skip_reason(_file("src/index.ts", content))
    assert reason is not None
    assert "barrel" in reason


def test_barrel_detection_ignores_blank_lines_and_comments():
    content = (
        "// re-export everything\n"
        "export * from './widget';\n"
        "\n"
        "/* block comment\n"
        "   spanning lines */\n"
        "export { Button } from './button'; // inline note\n"
    )
    assert nonsubstantive_filter.skip_reason(_file("src/index.ts", content)) is not None


def test_does_not_skip_files_with_real_logic_alongside_imports():
    content = "import { helper } from './helper';\n\nexport function run() {\n    return helper() + 1;\n}\n"
    assert nonsubstantive_filter.skip_reason(_file("src/run.js", content)) is None


def test_does_not_skip_ordinary_source_files():
    reason = nonsubstantive_filter.skip_reason(_file("src/widget.js", "const widget = () => 1;"))
    assert reason is None


def test_empty_file_is_not_treated_as_barrel():
    assert nonsubstantive_filter.skip_reason(_file("src/empty.js", "")) is None
