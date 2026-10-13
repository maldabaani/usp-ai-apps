import { NodeKind } from '../../../../services/devcrew.service';

// Hand-drawn (no icon library, matching the rest of this app) stroke-only
// glyphs, viewBox 0 0 24 24, one per WorkflowNode.kind -- rendered via a
// single shared <svg> in the template, fill="none" stroke="currentColor" so
// each glyph inherits its node card's status color automatically.
const ICON_PATHS: Record<NodeKind, string[]> = {
  input: ['M12 3v10m0 0l-3-3m3 3l3-3', 'M4 15h3l1.5 2h7l1.5-2h3v4a1 1 0 01-1 1H5a1 1 0 01-1-1v-4z'],
  agent: ['M20 12l-4 7.93H8L4 12l4-7.93h8L20 12z', 'M12 9a3 3 0 100 6 3 3 0 000-6z'],
  approval: ['M21 12a9 9 0 11-18 0 9 9 0 0118 0z', 'M8 12.5l2.5 2.5L16 9'],
  system: ['M20.3 15.4L15.4 20.3H8.6L3.7 15.4V8.6L8.6 3.7H15.4L20.3 8.6V15.4Z', 'M15 12a3 3 0 11-6 0 3 3 0 016 0z'],
  task: ['M9 8l-5 4 5 4', 'M15 8l5 4-5 4'],
  output: ['M6 3v18', 'M6 4h11l-2.5 3.5L17 11H6'],
};

export function nodeIconPaths(kind: NodeKind): string[] {
  return ICON_PATHS[kind] ?? ICON_PATHS.system;
}
