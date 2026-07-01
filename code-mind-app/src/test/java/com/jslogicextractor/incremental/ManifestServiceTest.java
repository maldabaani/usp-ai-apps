package com.jslogicextractor.incremental;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.jslogicextractor.config.ExtractionProperties;
import com.jslogicextractor.scanner.SourceFile;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;

class ManifestServiceTest {

    @TempDir
    Path tempDir;

    private ManifestService service() {
        ExtractionProperties props = new ExtractionProperties(tempDir, null, null, 300_000, 8, false, null);
        return new ManifestService(new ObjectMapper(), props);
    }

    @Test
    void returnsEmptyWhenNoManifestExists() {
        ManifestService service = service();
        assertThat(service.load(tempDir.resolve("repo"))).isEmpty();
    }

    @Test
    void savesAndLoadsManifestRoundTrip() {
        ManifestService service = service();
        Path repoRoot = tempDir.resolve("repo");
        Map<String, String> hashes = Map.of("src/a.js", "hash1", "src/b.ts", "hash2");
        Path outputDir = tempDir.resolve("output/job-123");

        service.save(repoRoot, new ManifestService.Manifest(outputDir, hashes));
        Optional<ManifestService.Manifest> loaded = service.load(repoRoot);

        assertThat(loaded).isPresent();
        assertThat(loaded.get().outputDirectory()).isEqualTo(outputDir.toAbsolutePath());
        assertThat(loaded.get().fileHashes()).isEqualTo(hashes);
    }

    @Test
    void differentRepRootsProduceDifferentManifestFiles() {
        ManifestService service = service();
        Path repoA = tempDir.resolve("repoA");
        Path repoB = tempDir.resolve("repoB");
        Map<String, String> hashesA = Map.of("a.js", "aaa");
        Map<String, String> hashesB = Map.of("b.js", "bbb");

        service.save(repoA, new ManifestService.Manifest(tempDir.resolve("outA"), hashesA));
        service.save(repoB, new ManifestService.Manifest(tempDir.resolve("outB"), hashesB));

        assertThat(service.load(repoA).get().fileHashes()).isEqualTo(hashesA);
        assertThat(service.load(repoB).get().fileHashes()).isEqualTo(hashesB);
    }

    @Test
    void computeHashesDeduplicatesChunkedFiles() throws IOException {
        ManifestService service = service();
        Path repoRoot = tempDir.resolve("repo");
        Files.createDirectories(repoRoot);
        Path file = repoRoot.resolve("big.js");
        Files.writeString(file, "const x = 1;");

        // Simulate two chunks from the same original file
        SourceFile chunk1 = new SourceFile(file, "big.js/part-0001.js", "const x", 7);
        SourceFile chunk2 = new SourceFile(file, "big.js/part-0002.js", " = 1;", 5);

        Map<String, String> hashes = service.computeHashes(repoRoot, List.of(chunk1, chunk2));

        // Should produce one entry keyed by the original file path, not by chunk path
        assertThat(hashes).hasSize(1);
        assertThat(hashes).containsKey("big.js");
    }

    @Test
    void computeHashesProducesConsistentHashForSameContent() throws IOException {
        ManifestService service = service();
        Path repoRoot = tempDir.resolve("repo");
        Files.createDirectories(repoRoot);
        Path file = repoRoot.resolve("a.js");
        Files.writeString(file, "const a = 1;");
        SourceFile sourceFile = new SourceFile(file, "a.js", "const a = 1;", 12);

        Map<String, String> first = service.computeHashes(repoRoot, List.of(sourceFile));
        Map<String, String> second = service.computeHashes(repoRoot, List.of(sourceFile));

        assertThat(first).isEqualTo(second);
        assertThat(first.get("a.js")).hasSize(64); // SHA-256 hex = 64 chars
    }

    @Test
    void computeHashesProducesDifferentHashAfterContentChange() throws IOException {
        ManifestService service = service();
        Path repoRoot = tempDir.resolve("repo");
        Files.createDirectories(repoRoot);
        Path file = repoRoot.resolve("a.js");
        Files.writeString(file, "const a = 1;");
        SourceFile before = new SourceFile(file, "a.js", "const a = 1;", 12);
        Map<String, String> hashBefore = service.computeHashes(repoRoot, List.of(before));

        Files.writeString(file, "const a = 2;");
        SourceFile after = new SourceFile(file, "a.js", "const a = 2;", 12);
        Map<String, String> hashAfter = service.computeHashes(repoRoot, List.of(after));

        assertThat(hashBefore.get("a.js")).isNotEqualTo(hashAfter.get("a.js"));
    }

    @Test
    void diffDetectsAddedModifiedAndDeletedFiles() {
        ManifestService service = service();
        Map<String, String> previous = Map.of(
                "src/unchanged.js", "hash-unchanged",
                "src/modified.js", "hash-old",
                "src/deleted.js", "hash-deleted");
        Map<String, String> current = Map.of(
                "src/unchanged.js", "hash-unchanged",
                "src/modified.js", "hash-new",
                "src/added.js", "hash-added");

        ManifestService.FileChanges changes = service.diff(previous, current);

        assertThat(changes.added()).containsExactly("src/added.js");
        assertThat(changes.modified()).containsExactly("src/modified.js");
        assertThat(changes.deleted()).containsExactly("src/deleted.js");
    }

    @Test
    void diffChangedOrAddedReturnsBothAddedAndModified() {
        ManifestService service = service();
        Map<String, String> previous = Map.of("old.js", "hash-old");
        Map<String, String> current = Map.of("old.js", "hash-new", "new.js", "hash-new2");

        ManifestService.FileChanges changes = service.diff(previous, current);

        assertThat(changes.changedOrAdded()).containsExactlyInAnyOrder("old.js", "new.js");
    }

    @Test
    void diffReturnsNoChangesForIdenticalManifests() {
        ManifestService service = service();
        Map<String, String> hashes = Map.of("a.js", "h1", "b.js", "h2");

        ManifestService.FileChanges changes = service.diff(hashes, hashes);

        assertThat(changes.added()).isEmpty();
        assertThat(changes.modified()).isEmpty();
        assertThat(changes.deleted()).isEmpty();
    }
}
