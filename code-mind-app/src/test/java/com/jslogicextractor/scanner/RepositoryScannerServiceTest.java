package com.jslogicextractor.scanner;

import com.jslogicextractor.config.ChunkingProperties;
import com.jslogicextractor.config.ExtractionProperties;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class RepositoryScannerServiceTest {

    @TempDir
    Path repoRoot;

    private RepositoryScannerService scanner;

    @BeforeEach
    void setUp() {
        ExtractionProperties properties = new ExtractionProperties(null, null, null, 300_000, 8, true, null);
        ChunkingProperties chunkingProperties = new ChunkingProperties(false, 0);
        scanner = new RepositoryScannerService(properties, chunkingProperties, new LargeFileChunker(chunkingProperties));
    }

    @Test
    void scansIncludedExtensionsAndSkipsExcludedDirectories() throws IOException {
        write(repoRoot.resolve("src/index.js"), "console.log('hi');");
        write(repoRoot.resolve("src/app.tsx"), "export const App = () => null;");
        write(repoRoot.resolve("README.md"), "# docs");
        write(repoRoot.resolve("node_modules/lib/index.js"), "module.exports = {};");
        write(repoRoot.resolve("dist/bundle.js"), "/* generated */");

        List<SourceFile> files = scanner.scan(repoRoot);

        assertThat(files).extracting(SourceFile::relativePath)
                .containsExactlyInAnyOrder("src/index.js", "src/app.tsx");
    }

    @Test
    void skipsFilesLargerThanMaxSizeWhenChunkingDisabled() throws IOException {
        ExtractionProperties tightProperties = new ExtractionProperties(null, null, null, 10, 8, true, null);
        ChunkingProperties chunkingDisabled = new ChunkingProperties(false, 0);
        scanner = new RepositoryScannerService(tightProperties, chunkingDisabled, new LargeFileChunker(chunkingDisabled));
        write(repoRoot.resolve("big.js"), "x".repeat(100));

        List<SourceFile> files = scanner.scan(repoRoot);

        assertThat(files).isEmpty();
    }

    @Test
    void splitsFilesLargerThanMaxSizeIntoChunksWhenChunkingEnabled() throws IOException {
        ExtractionProperties tightProperties = new ExtractionProperties(null, null, null, 10, 8, true, null);
        ChunkingProperties chunkingEnabled = new ChunkingProperties(true, 1);
        scanner = new RepositoryScannerService(tightProperties, chunkingEnabled, new LargeFileChunker(chunkingEnabled));
        write(repoRoot.resolve("big.js"), "const a = 1;\nconst b = 2;\nconst c = 3;");

        List<SourceFile> files = scanner.scan(repoRoot);

        assertThat(files).extracting(SourceFile::relativePath)
                .containsExactly("big.js/part-0001.js", "big.js/part-0002.js", "big.js/part-0003.js");
    }

    @Test
    void rejectsNonDirectoryRoot() throws IOException {
        Path file = repoRoot.resolve("not-a-dir.txt");
        write(file, "x");

        assertThatThrownBy(() -> scanner.scan(file)).isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    void scanFileReturnsSingleSourceFileWithIncludedExtension() throws IOException {
        Path file = repoRoot.resolve("dropped.js");
        write(file, "const x = 1;");

        List<SourceFile> files = scanner.scanFile(file);

        assertThat(files).extracting(SourceFile::relativePath).containsExactly("dropped.js");
    }

    @Test
    void scanFileSkipsFilesWithExcludedExtension() throws IOException {
        Path file = repoRoot.resolve("notes.txt");
        write(file, "not js");

        List<SourceFile> files = scanner.scanFile(file);

        assertThat(files).isEmpty();
    }

    @Test
    void scanFileRejectsNonFilePath() throws IOException {
        Path directory = repoRoot.resolve("a-directory");
        Files.createDirectories(directory);

        assertThatThrownBy(() -> scanner.scanFile(directory)).isInstanceOf(IllegalArgumentException.class);
    }

    private void write(Path path, String content) throws IOException {
        Files.createDirectories(path.getParent());
        Files.writeString(path, content);
    }
}
