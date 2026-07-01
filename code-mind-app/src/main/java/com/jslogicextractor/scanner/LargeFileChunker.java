package com.jslogicextractor.scanner;

import com.jslogicextractor.config.ChunkingProperties;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

/**
 * Splits a single oversized source file into multiple smaller {@link SourceFile} chunks so it can
 * flow through the existing per-file extraction pipeline unchanged. Cuts are only ever made at
 * line boundaries, and preferentially at "depth-0" boundaries (outside any bracket/paren/brace
 * nesting, string, or block comment) so a chunk rarely splits a function/class/block in half.
 *
 * <p>Bracket depth is tracked with a single combined counter across {@code {}}, {@code ()}, and
 * {@code []}. Template literals are treated as one opaque string region (interpolation internals
 * are not tracked), and regex literals are not specially detected, so their characters are scanned
 * at face value. Both are accepted simplifications: a miscounted depth can only ever delay a cut to
 * a later line, never corrupt chunk content, since every cut lands exactly on a line boundary.
 */
@Component
public class LargeFileChunker {

    private static final Logger log = LoggerFactory.getLogger(LargeFileChunker.class);
    private static final int HARD_CAP_MULTIPLIER = 2;

    private final ChunkingProperties properties;

    public LargeFileChunker(ChunkingProperties properties) {
        this.properties = properties;
    }

    public List<SourceFile> chunk(Path absolutePath, String relativePath, String content) {
        String[] lines = content.split("\n", -1);
        if (lines.length <= 1) {
            log.warn("{} has no line breaks to split on ({} chars); sending as a single oversized chunk",
                    relativePath, content.length());
            return List.of(toChunkSourceFile(absolutePath, relativePath, content, 1));
        }

        int hardCapLines = properties.maxLinesPerChunk() * HARD_CAP_MULTIPLIER;
        List<SourceFile> chunks = new ArrayList<>();
        StringBuilder buffer = new StringBuilder();
        ScanState state = new ScanState();
        int linesInChunk = 0;

        for (int idx = 0; idx < lines.length; idx++) {
            String line = lines[idx];
            if (buffer.length() > 0) {
                buffer.append('\n');
            }
            buffer.append(line);
            linesInChunk++;

            scanLine(line, state);

            boolean atSafeBoundary = state.depth <= 0 && state.stringDelim == 0 && !state.inBlockComment;
            boolean reachedTarget = linesInChunk >= properties.maxLinesPerChunk();
            boolean reachedHardCap = linesInChunk >= hardCapLines;
            boolean isLastLine = idx == lines.length - 1;

            if (isLastLine || (reachedTarget && atSafeBoundary) || reachedHardCap) {
                if (reachedHardCap && !atSafeBoundary && !isLastLine) {
                    log.warn("Force-cutting {} chunk {} after {} lines without reaching a safe boundary (bracket depth={})",
                            relativePath, chunks.size() + 1, linesInChunk, state.depth);
                }
                chunks.add(toChunkSourceFile(absolutePath, relativePath, buffer.toString(), chunks.size() + 1));
                buffer.setLength(0);
                linesInChunk = 0;
                state.reset();
            }
        }

        return chunks;
    }

    private void scanLine(String line, ScanState state) {
        int n = line.length();
        int i = 0;
        while (i < n) {
            char c = line.charAt(i);

            if (state.stringDelim != 0) {
                if (c == '\\') {
                    i += 2;
                    continue;
                }
                if (c == state.stringDelim) {
                    state.stringDelim = 0;
                }
                i++;
                continue;
            }

            if (state.inBlockComment) {
                if (c == '*' && i + 1 < n && line.charAt(i + 1) == '/') {
                    state.inBlockComment = false;
                    i += 2;
                    continue;
                }
                i++;
                continue;
            }

            if (c == '/' && i + 1 < n && line.charAt(i + 1) == '/') {
                return;
            }
            if (c == '/' && i + 1 < n && line.charAt(i + 1) == '*') {
                state.inBlockComment = true;
                i += 2;
                continue;
            }
            if (c == '\'' || c == '"' || c == '`') {
                state.stringDelim = c;
                i++;
                continue;
            }
            if (c == '{' || c == '(' || c == '[') {
                state.depth++;
                i++;
                continue;
            }
            if (c == '}' || c == ')' || c == ']') {
                state.depth--;
                i++;
                continue;
            }
            i++;
        }
    }

    private SourceFile toChunkSourceFile(Path absolutePath, String relativePath, String content, int partNumber) {
        String chunkRelativePath = "%s/part-%04d%s".formatted(relativePath, partNumber, extractExtension(relativePath));
        long sizeBytes = content.getBytes(StandardCharsets.UTF_8).length;
        return new SourceFile(absolutePath, chunkRelativePath, content, sizeBytes);
    }

    private String extractExtension(String relativePath) {
        int slash = relativePath.lastIndexOf('/');
        int dot = relativePath.lastIndexOf('.');
        return dot > slash ? relativePath.substring(dot) : "";
    }

    private static final class ScanState {
        int depth;
        char stringDelim;
        boolean inBlockComment;

        void reset() {
            depth = 0;
            stringDelim = 0;
            inBlockComment = false;
        }
    }
}
