package com.jslogicextractor.scanner;

import com.jslogicextractor.config.ChunkingProperties;
import org.junit.jupiter.api.Test;

import java.nio.file.Path;
import java.util.List;
import java.util.stream.Collectors;

import static org.assertj.core.api.Assertions.assertThat;

class LargeFileChunkerTest {

    private final Path absolutePath = Path.of("/repo/big.js");

    @Test
    void contentUnderTargetLineCountReturnsSingleChunk() {
        LargeFileChunker chunker = new LargeFileChunker(new ChunkingProperties(true, 100));

        List<SourceFile> chunks = chunker.chunk(absolutePath, "big.js", "const a = 1;\nconst b = 2;");

        assertThat(chunks).hasSize(1);
        assertThat(chunks.get(0).relativePath()).isEqualTo("big.js/part-0001.js");
        assertThat(chunks.get(0).content()).isEqualTo("const a = 1;\nconst b = 2;");
    }

    @Test
    void splitsAtSafeBoundariesNearTargetLineCount() {
        LargeFileChunker chunker = new LargeFileChunker(new ChunkingProperties(true, 2));
        String content = "const a = 1;\nconst b = 2;\nconst c = 3;\nconst d = 4;";

        List<SourceFile> chunks = chunker.chunk(absolutePath, "big.js", content);

        assertThat(chunks).extracting(SourceFile::relativePath)
                .containsExactly("big.js/part-0001.js", "big.js/part-0002.js");
        assertThat(chunks.get(0).content()).isEqualTo("const a = 1;\nconst b = 2;");
        assertThat(chunks.get(1).content()).isEqualTo("const c = 3;\nconst d = 4;");
    }

    @Test
    void waitsForSafeBoundaryWhenBlockSpansTargetLineCount() {
        LargeFileChunker chunker = new LargeFileChunker(new ChunkingProperties(true, 2));
        String content = String.join("\n",
                "function foo() {",
                "  doStuff();",
                "  doMore();",
                "}",
                "function bar() {",
                "  doStuff();",
                "}");

        List<SourceFile> chunks = chunker.chunk(absolutePath, "big.js", content);

        assertThat(chunks).hasSize(2);
        assertThat(chunks.get(0).content()).isEqualTo(
                "function foo() {\n  doStuff();\n  doMore();\n}");
        assertThat(chunks.get(1).content()).isEqualTo(
                "function bar() {\n  doStuff();\n}");
    }

    @Test
    void hardCapForcesACutWhenABlockNeverReturnsToDepthZero() {
        LargeFileChunker chunker = new LargeFileChunker(new ChunkingProperties(true, 2));
        String content = String.join("\n",
                "function foo() {",
                "  a();",
                "  b();",
                "  c();",
                "  d();",
                "  e();",
                "}");

        List<SourceFile> chunks = chunker.chunk(absolutePath, "big.js", content);

        assertThat(chunks).hasSize(3);
        assertThat(chunks.get(0).content()).isEqualTo(
                "function foo() {\n  a();\n  b();\n  c();");
        assertThat(chunks.get(1).content()).isEqualTo("  d();\n  e();");
        assertThat(chunks.get(2).content()).isEqualTo("}");
    }

    @Test
    void rejoiningChunksReconstructsOriginalContent() {
        LargeFileChunker chunker = new LargeFileChunker(new ChunkingProperties(true, 2));
        String content = "const a = 1;\nconst b = 2;\nconst c = 3;\nconst d = 4;";

        List<SourceFile> chunks = chunker.chunk(absolutePath, "big.js", content);

        String rejoined = chunks.stream().map(SourceFile::content).collect(Collectors.joining("\n"));
        assertThat(rejoined).isEqualTo(content);
    }

    @Test
    void singleLineFileWithNoLineBreaksIsSentAsOneChunk() {
        LargeFileChunker chunker = new LargeFileChunker(new ChunkingProperties(true, 100));
        String content = "x".repeat(500);

        List<SourceFile> chunks = chunker.chunk(absolutePath, "tiny.js", content);

        assertThat(chunks).hasSize(1);
        assertThat(chunks.get(0).relativePath()).isEqualTo("tiny.js/part-0001.js");
        assertThat(chunks.get(0).content()).isEqualTo(content);
    }
}
