package com.jslogicextractor.incremental;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.jslogicextractor.config.ExtractionProperties;
import com.jslogicextractor.scanner.SourceFile;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;

/**
 * Persists a per-repository content-hash manifest so the orchestrator can detect which source files
 * changed between runs and process only those (incremental mode).
 *
 * <p>Manifests are stored at {@code <defaultOutputDir>/.manifests/<sha256(repoRoot)>.json}, keyed
 * by a hash of the repository root path. This makes them discoverable from the repo path alone,
 * independent of per-job output directories.
 */
@Service
public class ManifestService {

    private static final Logger log = LoggerFactory.getLogger(ManifestService.class);

    private final ObjectMapper objectMapper;
    private final Path manifestsDir;

    public ManifestService(ObjectMapper objectMapper, ExtractionProperties properties) {
        this.objectMapper = objectMapper;
        this.manifestsDir = properties.defaultOutputDirectory().toAbsolutePath().resolve(".manifests");
    }

    public Optional<Manifest> load(Path repoRoot) {
        Path file = manifestPath(repoRoot);
        if (!Files.exists(file)) {
            return Optional.empty();
        }
        try {
            ManifestJson json = objectMapper.readValue(file.toFile(), ManifestJson.class);
            return Optional.of(new Manifest(
                    Path.of(json.outputDirectory()).toAbsolutePath(),
                    json.fileHashes()));
        } catch (IOException e) {
            log.warn("Could not read manifest at {}, treating as full run: {}", file, e.getMessage());
            return Optional.empty();
        }
    }

    public void save(Path repoRoot, Manifest manifest) {
        Path file = manifestPath(repoRoot);
        try {
            Files.createDirectories(file.getParent());
            ManifestJson json = new ManifestJson(
                    manifest.outputDirectory().toString(),
                    manifest.fileHashes());
            Files.writeString(file,
                    objectMapper.writerWithDefaultPrettyPrinter().writeValueAsString(json));
        } catch (IOException e) {
            log.warn("Could not save manifest at {}: {}", file, e.getMessage());
        }
    }

    /**
     * Computes SHA-256 content hashes for the distinct original source files.
     * Chunked {@link SourceFile}s share the same {@code absolutePath}, so they are
     * deduplicated before hashing — the whole original file is always hashed from disk.
     */
    public Map<String, String> computeHashes(Path repoRoot, List<SourceFile> files) {
        Set<Path> seen = new LinkedHashSet<>();
        Map<String, String> hashes = new LinkedHashMap<>();
        for (SourceFile file : files) {
            if (seen.add(file.absolutePath())) {
                String relPath = repoRoot.relativize(file.absolutePath()).toString();
                String hash = sha256File(file.absolutePath());
                if (hash != null) {
                    hashes.put(relPath, hash);
                }
            }
        }
        return hashes;
    }

    public FileChanges diff(Map<String, String> previous, Map<String, String> current) {
        List<String> added = new ArrayList<>();
        List<String> modified = new ArrayList<>();
        List<String> deleted = new ArrayList<>();

        for (Map.Entry<String, String> entry : current.entrySet()) {
            String prevHash = previous.get(entry.getKey());
            if (prevHash == null) {
                added.add(entry.getKey());
            } else if (!entry.getValue().equals(prevHash)) {
                modified.add(entry.getKey());
            }
        }
        for (String key : previous.keySet()) {
            if (!current.containsKey(key)) {
                deleted.add(key);
            }
        }
        return new FileChanges(added, modified, deleted);
    }

    private Path manifestPath(Path repoRoot) {
        String hash = sha256Hex(repoRoot.toAbsolutePath().toString().getBytes(StandardCharsets.UTF_8));
        return manifestsDir.resolve(hash + ".json");
    }

    private String sha256File(Path file) {
        try {
            return sha256Hex(Files.readAllBytes(file));
        } catch (IOException e) {
            log.warn("Cannot hash {}: {}", file, e.getMessage());
            return null;
        }
    }

    private String sha256Hex(byte[] bytes) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            byte[] hash = digest.digest(bytes);
            StringBuilder sb = new StringBuilder(hash.length * 2);
            for (byte b : hash) {
                sb.append(String.format("%02x", b));
            }
            return sb.toString();
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException("SHA-256 unavailable", e);
        }
    }

    public record Manifest(Path outputDirectory, Map<String, String> fileHashes) {}

    public record FileChanges(List<String> added, List<String> modified, List<String> deleted) {
        public List<String> changedOrAdded() {
            List<String> result = new ArrayList<>(added);
            result.addAll(modified);
            return result;
        }
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    private record ManifestJson(String outputDirectory, Map<String, String> fileHashes) {}
}
