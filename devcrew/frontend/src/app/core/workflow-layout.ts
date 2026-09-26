/** Pure helpers for the workflow graph: layered left-to-right layout, icons, durations. */

import { WorkflowEdge, WorkflowNode } from './api.models';

export interface Point {
  x: number;
  y: number;
}

export interface Size {
  width: number;
  height: number;
}

export const STAGE_SIZE: Size = { width: 196, height: 92 };
export const TASK_SIZE: Size = { width: 236, height: 118 };
const COLUMN_GAP = 84;
const ROW_GAP = 40;

export function nodeSize(node: Pick<WorkflowNode, 'kind'>): Size {
  return node.kind === 'task' ? TASK_SIZE : STAGE_SIZE;
}

/**
 * Rank = longest path from a source over `flow` edges (loop edges are ignored), so every node
 * sits right of everything it depends on and parallel tasks share a column.
 */
export function ranks(nodes: readonly WorkflowNode[], edges: readonly WorkflowEdge[]): Map<string, number> {
  const ids = new Set(nodes.map((n) => n.id));
  const incoming = new Map<string, string[]>(nodes.map((n) => [n.id, []]));
  for (const e of edges) {
    if (e.kind === 'flow' && ids.has(e.source) && ids.has(e.target)) {
      incoming.get(e.target)?.push(e.source);
    }
  }
  const rank = new Map<string, number>();
  const visiting = new Set<string>();
  const visit = (id: string): number => {
    const known = rank.get(id);
    if (known !== undefined) {
      return known;
    }
    if (visiting.has(id)) {
      return 0; // cycle guard
    }
    visiting.add(id);
    const preds = incoming.get(id) ?? [];
    const value = preds.length ? Math.max(...preds.map(visit)) + 1 : 0;
    visiting.delete(id);
    rank.set(id, value);
    return value;
  };
  nodes.forEach((n) => visit(n.id));
  return rank;
}

/** Top-left positions: columns by rank, each column centered on the main row (y = 0). */
export function layoutWorkflow(
  nodes: readonly WorkflowNode[],
  edges: readonly WorkflowEdge[],
): Map<string, Point> {
  const rank = ranks(nodes, edges);
  const columns = new Map<number, WorkflowNode[]>();
  for (const node of nodes) {
    const r = rank.get(node.id) ?? 0;
    columns.set(r, [...(columns.get(r) ?? []), node]);
  }
  const widths = new Map<number, number>();
  for (const [r, col] of columns) {
    widths.set(r, Math.max(...col.map((n) => nodeSize(n).width)));
  }
  const maxRank = Math.max(0, ...columns.keys());
  const xs: number[] = [];
  let x = 0;
  for (let r = 0; r <= maxRank; r++) {
    xs.push(x);
    x += (widths.get(r) ?? STAGE_SIZE.width) + COLUMN_GAP;
  }

  const positions = new Map<string, Point>();
  for (const [r, col] of columns) {
    const heights = col.map((n) => nodeSize(n).height);
    const total = heights.reduce((a, b) => a + b, 0) + ROW_GAP * (col.length - 1);
    let y = -total / 2;
    col.forEach((node, i) => {
      const size = nodeSize(node);
      const colWidth = widths.get(r) ?? size.width;
      positions.set(node.id, { x: xs[r] + (colWidth - size.width) / 2, y });
      y += heights[i] + ROW_GAP;
    });
  }
  return positions;
}

/** Which icon a node shows: the agent currently working on it, or the stage's glyph. */
export function iconKind(node: WorkflowNode): string {
  switch (node.kind) {
    case 'agent':
      return node.id; // planner | architect
    case 'task':
      if (node.step === 'reviewer' || node.step === 'qa' || node.step === 'coordinator') {
        return node.step;
      }
      if (node.step === 'escalate') {
        return 'coordinator';
      }
      return 'developer';
    case 'approval':
      return 'approval';
    case 'input':
      return 'requirements';
    case 'output':
      return 'delivery';
    default:
      if (node.id === 'prepare_repo') {
        return 'repository';
      }
      return node.id === 'gates' ? 'gates' : 'system';
  }
}

export function formatDuration(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) {
    return '';
  }
  const s = Math.floor(ms / 1000);
  if (s < 60) {
    return `${s}s`;
  }
  const m = Math.floor(s / 60);
  if (m < 60) {
    return `${m}m ${String(s % 60).padStart(2, '0')}s`;
  }
  return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, '0')}m`;
}

/** Time spent in a node: until it finished, or until `now` while it is still active. */
export function nodeElapsed(node: WorkflowNode, now: number): string {
  if (!node.started_at) {
    return '';
  }
  const start = Date.parse(node.started_at);
  const active = node.status === 'running' || node.status === 'waiting';
  const end = active || !node.finished_at ? now : Date.parse(node.finished_at);
  if (!active && !node.finished_at) {
    return '';
  }
  return formatDuration(end - start);
}

/** Structure signature: the graph is rebuilt (re-laid out) only when this changes. */
export function structureKey(nodes: readonly WorkflowNode[], edges: readonly WorkflowEdge[]): string {
  return `${nodes.map((n) => n.id).join('|')}#${edges.map((e) => e.id).join('|')}`;
}
