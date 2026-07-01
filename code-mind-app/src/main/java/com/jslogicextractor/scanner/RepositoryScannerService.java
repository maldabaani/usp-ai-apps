package com.jslogicextractor.scanner;

import com.jslogicextractor.config.ChunkingProperties;
import com.jslogicextractor.config.ExtractionProperties;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Locale;
import java.util.stream.Stream;

@Service
public class RepositoryScannerService {

    private static final Logger log = LoggerFactory.getLogger(RepositoryScannerService.class);

    private final ExtractionProperties properties;
    private final ChunkingProperties chunkingProperties;
    private final LargeFileChunker chunker;

    public RepositoryScannerService(ExtractionProperties properties, ChunkingProperties chunkingProperties,
                                     LargeFileChunker chunker) {
        this.properties = properties;
        this.chunkingProperties = chunkingProperties;
        this.chunker = chunker;
    }

    public List<SourceFile> scan(Path repositoryRoot) {
        if (!Files.isDirectory(repositoryRoot)) {
            throw new IllegalArgumentException("Not a directory: " + repositoryRoot);
        }
        try (Stream<Path> walk = Files.walk(repositoryRoot)) {
            return walk
                    .filter(Files::isRegularFile)
                    .filter(path -> !isExcluded(repositoryRoot, path))
                    .filter(this::hasIncludedExtension)
                    .map(path -> readSourceFiles(repositoryRoot, path))
                    .flatMap(List::stream)
                    .toList();
        } catch (IOException e) {
            throw new UncheckedIOException("Failed to scan repository: " + repositoryRoot, e);
        }
    }

    /**
     * Single-file counterpart to {@link #scan(Path)}, used by the input-directory watcher: each
     * dropped file becomes its own job scanning exactly that one file rather than a whole directory.
     */
    public List<SourceFile> scanFile(Path file) {
        if (!Files.isRegularFile(file)) {
            throw new IllegalArgumentException("Not a file: " + file);
        }
        if (!hasIncludedExtension(file)) {
            return List.of();
        }
        return readSourceFiles(file.getParent(), file);
    }

    private boolean isExcluded(Path root, Path file) {
        Path relative = root.relativize(file);
        for (Path segment : relative) {
            if (properties.excludedDirectoryNames().contains(segment.toString())) {
                return true;
            }
        }
        return false;
    }

    private boolean hasIncludedExtension(Path file) {
        String name = file.getFileName().toString().toLowerCase(Locale.ROOT);
        return properties.includedExtensions().stream().anyMatch(name::endsWith);
    }

    private List<SourceFile> readSourceFiles(Path root, Path file) {
        try {
            long size = Files.size(file);
            String relativePath = root.relativize(file).toString();
            if (size > properties.maxFileSizeBytes()) {
                if (!chunkingProperties.enabled()) {
                    log.warn("Skipping {} ({} bytes exceeds max-file-size-bytes={})", file, size, properties.maxFileSizeBytes());
                    return List.of();
                }
                String content = Files.readString(file, StandardCharsets.UTF_8);
                List<SourceFile> chunks = chunker.chunk(file, relativePath, content);
                log.info("Split {} ({} bytes) into {} chunk(s)", file, size, chunks.size());
                return chunks;
            }
            String content = Files.readString(file, StandardCharsets.UTF_8);
            return List.of(new SourceFile(file, relativePath, content, size));
        } catch (IOException e) {
            log.warn("Skipping unreadable file {}: {}", file, e.getMessage());
            return List.of();
        }
    }
}
