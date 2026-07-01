package com.jslogicextractor.filter;

import com.jslogicextractor.scanner.SourceFile;
import org.springframework.stereotype.Component;

import java.util.Optional;
import java.util.regex.Pattern;

/**
 * Cheap, regex-only pre-pass applied to every file regardless of execution mode (sync or batch):
 * skips files that would burn a Claude call on output with no real business logic to extract.
 * Deliberately conservative — a false negative just costs one wasted call, but a false positive
 * silently drops a file's extraction entirely, so each rule only matches unambiguous cases.
 */
@Component
public class NonSubstantiveFileFilter {

    private static final Pattern TEST_FILENAME = Pattern.compile("(?i).*\\.(test|spec)\\.[jt]sx?$");
    private static final Pattern TEST_PATH_SEGMENT = Pattern.compile("(?i)(^|/)(__tests__|tests?)(/|$)");
    private static final Pattern IMPORT_LINE = Pattern.compile("^import\\b.*$");
    private static final Pattern EXPORT_FROM_LINE =
            Pattern.compile("^export\\s+(type\\s+)?(\\*(?:\\s+as\\s+\\w+)?|\\{[^}]*\\})\\s+from\\s+['\"][^'\"]+['\"];?$");
    private static final Pattern BLOCK_COMMENT = Pattern.compile("/\\*.*?\\*/", Pattern.DOTALL);

    public Optional<String> skipReason(SourceFile file) {
        String path = file.relativePath();

        if (path.endsWith(".d.ts")) {
            return Optional.of("type-declaration file (.d.ts)");
        }
        if (TEST_FILENAME.matcher(path).matches() || TEST_PATH_SEGMENT.matcher(path).find()) {
            return Optional.of("test/spec file");
        }
        if (isBarrelFile(file.content())) {
            return Optional.of("barrel file (re-exports only)");
        }
        return Optional.empty();
    }

    private boolean isBarrelFile(String content) {
        String withoutBlockComments = BLOCK_COMMENT.matcher(content).replaceAll("");
        boolean sawImportOrExportLine = false;
        for (String rawLine : withoutBlockComments.split("\n")) {
            String line = stripLineComment(rawLine).trim();
            if (line.isEmpty()) {
                continue;
            }
            if (!IMPORT_LINE.matcher(line).matches() && !EXPORT_FROM_LINE.matcher(line).matches()) {
                return false;
            }
            sawImportOrExportLine = true;
        }
        return sawImportOrExportLine;
    }

    private String stripLineComment(String line) {
        int idx = line.indexOf("//");
        return idx >= 0 ? line.substring(0, idx) : line;
    }
}
