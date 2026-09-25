/** Unified diff parsing for the diff viewer. */

export type DiffLineKind = 'add' | 'del' | 'ctx' | 'meta';

export interface DiffLine {
  kind: DiffLineKind;
  text: string;
  oldNo: number | null;
  newNo: number | null;
}

export interface DiffHunk {
  header: string;
  lines: DiffLine[];
}

export interface DiffFile {
  path: string;
  oldPath: string | null;
  added: number;
  removed: number;
  hunks: DiffHunk[];
}

const HUNK_RE = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/;

export function parseUnifiedDiff(text: string): DiffFile[] {
  const files: DiffFile[] = [];
  let file: DiffFile | null = null;
  let hunk: DiffHunk | null = null;
  let oldNo = 0;
  let newNo = 0;

  for (const line of text.split('\n')) {
    if (line.startsWith('diff --git ')) {
      const match = /^diff --git a\/(.+) b\/(.+)$/.exec(line);
      file = { path: match?.[2] ?? line, oldPath: match?.[1] ?? null, added: 0, removed: 0, hunks: [] };
      files.push(file);
      hunk = null;
      continue;
    }
    if (!file) {
      continue;
    }
    if (line.startsWith('+++ ') || line.startsWith('--- ')) {
      if (line.startsWith('+++ ') && line !== '+++ /dev/null') {
        file.path = line.slice(4).replace(/^b\//, '');
      }
      continue;
    }
    const header = HUNK_RE.exec(line);
    if (header) {
      oldNo = Number(header[1]);
      newNo = Number(header[2]);
      hunk = { header: line, lines: [] };
      file.hunks.push(hunk);
      continue;
    }
    if (!hunk) {
      continue; // index/mode lines
    }
    if (line.startsWith('+')) {
      hunk.lines.push({ kind: 'add', text: line.slice(1), oldNo: null, newNo: newNo++ });
      file.added++;
    } else if (line.startsWith('-')) {
      hunk.lines.push({ kind: 'del', text: line.slice(1), oldNo: oldNo++, newNo: null });
      file.removed++;
    } else if (line.startsWith('\\')) {
      hunk.lines.push({ kind: 'meta', text: line, oldNo: null, newNo: null });
    } else if (line.startsWith(' ')) {
      hunk.lines.push({ kind: 'ctx', text: line.slice(1), oldNo: oldNo++, newNo: newNo++ });
    }
  }
  return files;
}
