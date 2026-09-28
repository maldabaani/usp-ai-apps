import { FileEntry } from './api.models';

/** One visible row of the Files tab's folder tree. */
export interface TreeRow {
  kind: 'dir' | 'file';
  path: string;
  name: string;
  depth: number;
  size: number;
  files: number; // files below a folder
}

interface Dir {
  dirs: Map<string, Dir>;
  files: FileEntry[];
  count: number;
}

function build(files: FileEntry[]): Dir {
  const root: Dir = { dirs: new Map(), files: [], count: 0 };
  for (const f of files) {
    const parts = f.path.split('/');
    let dir = root;
    dir.count++;
    for (const part of parts.slice(0, -1)) {
      let next = dir.dirs.get(part);
      if (!next) {
        next = { dirs: new Map(), files: [], count: 0 };
        dir.dirs.set(part, next);
      }
      next.count++;
      dir = next;
    }
    dir.files.push(f);
  }
  return root;
}

/**
 * Folder tree rows: folders first (sorted), then files. Only children of expanded folders are
 * listed. A filter shows the matching files with all their folders open.
 */
export function treeRows(files: FileEntry[], expanded: ReadonlySet<string>, filter = ''): TreeRow[] {
  const needle = filter.trim().toLowerCase();
  const shown = needle ? files.filter((f) => f.path.toLowerCase().includes(needle)) : files;
  const rows: TreeRow[] = [];
  const walk = (dir: Dir, prefix: string, depth: number): void => {
    for (const name of [...dir.dirs.keys()].sort((a, b) => a.localeCompare(b))) {
      const sub = dir.dirs.get(name)!;
      const path = prefix + name;
      rows.push({ kind: 'dir', path, name, depth, size: 0, files: sub.count });
      if (needle || expanded.has(path)) {
        walk(sub, `${path}/`, depth + 1);
      }
    }
    for (const f of [...dir.files].sort((a, b) => a.path.localeCompare(b.path))) {
      rows.push({ kind: 'file', path: f.path, name: f.path.slice(prefix.length), depth, size: f.size, files: 0 });
    }
  };
  walk(build(shown), '', 0);
  return rows;
}

/** Folders to open at first: all of them for small projects, else none. */
export function initialExpanded(files: FileEntry[], openAllBelow = 40): Set<string> {
  if (files.length > openAllBelow) {
    return new Set();
  }
  const dirs = new Set<string>();
  for (const f of files) {
    const parts = f.path.split('/');
    for (let i = 1; i < parts.length; i++) {
      dirs.add(parts.slice(0, i).join('/'));
    }
  }
  return dirs;
}
