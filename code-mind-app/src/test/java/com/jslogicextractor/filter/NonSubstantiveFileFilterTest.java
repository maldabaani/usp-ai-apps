package com.jslogicextractor.filter;

import com.jslogicextractor.scanner.SourceFile;
import org.junit.jupiter.api.Test;

import java.nio.file.Path;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;

class NonSubstantiveFileFilterTest {

    private final NonSubstantiveFileFilter filter = new NonSubstantiveFileFilter();

    @Test
    void skipsTypeDeclarationFiles() {
        Optional<String> reason = filter.skipReason(file("src/types/index.d.ts", "export type Foo = string;"));

        assertThat(reason).isPresent();
        assertThat(reason.get()).contains("type-declaration");
    }

    @Test
    void skipsTestFilenameSuffixes() {
        assertThat(filter.skipReason(file("src/Widget.test.ts", "test('x', () => {});"))).isPresent();
        assertThat(filter.skipReason(file("src/Widget.spec.jsx", "describe('x', () => {});"))).isPresent();
    }

    @Test
    void skipsFilesUnderTestDirectories() {
        assertThat(filter.skipReason(file("__tests__/widget.js", "test('x', () => {});"))).isPresent();
        assertThat(filter.skipReason(file("src/tests/helpers.js", "module.exports = {};"))).isPresent();
        assertThat(filter.skipReason(file("src/test/helpers.js", "module.exports = {};"))).isPresent();
    }

    @Test
    void skipsBarrelFilesContainingOnlyImportsAndReExports() {
        String content = """
                import './polyfills';
                export * from './widget';
                export { Button } from './button';
                export type { Props } from './props';
                """;

        Optional<String> reason = filter.skipReason(file("src/index.ts", content));

        assertThat(reason).isPresent();
        assertThat(reason.get()).contains("barrel");
    }

    @Test
    void barrelDetectionIgnoresBlankLinesAndComments() {
        String content = """
                // re-export everything
                export * from './widget';

                /* block comment
                   spanning lines */
                export { Button } from './button'; // inline note
                """;

        assertThat(filter.skipReason(file("src/index.ts", content))).isPresent();
    }

    @Test
    void doesNotSkipFilesWithRealLogicAlongsideImports() {
        String content = """
                import { helper } from './helper';

                export function run() {
                    return helper() + 1;
                }
                """;

        assertThat(filter.skipReason(file("src/run.js", content))).isEmpty();
    }

    @Test
    void doesNotSkipOrdinarySourceFiles() {
        Optional<String> reason = filter.skipReason(file("src/widget.js", "const widget = () => 1;"));

        assertThat(reason).isEmpty();
    }

    @Test
    void emptyFileIsNotTreatedAsBarrel() {
        assertThat(filter.skipReason(file("src/empty.js", ""))).isEmpty();
    }

    private SourceFile file(String relativePath, String content) {
        return new SourceFile(Path.of(relativePath), relativePath, content, content.length());
    }
}
