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
      return node.id.startsWith('followup:') ? 'coordinator' : node.id; // planner | architect
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
      if (node.id === 'watch') {
        return 'watch';
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

// ------------------------------------------------------------------ compact overview (Phase 16)
export const GROUP_PREFIX = 'group:';
const FINISHED = new Set(['done', 'skipped']);

export interface CompactResult {
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  /** group id -> the node ids it stands for */
  groups: Map<string, string[]>;
}

function groupNode(id: string, label: string, detail: string, members: WorkflowNode[]): WorkflowNode {
  const started = members.map((n) => n.started_at).filter((t): t is string => !!t).sort();
  const finished = members.map((n) => n.finished_at).filter((t): t is string => !!t).sort();
  return {
    id,
    kind: 'system',
    label,
    status: members.every((n) => n.status === 'skipped') ? 'skipped' : 'done',
    detail,
    step: null,
    task_id: null,
    stack: null,
    started_at: started[0] ?? null,
    finished_at: finished[finished.length - 1] ?? null,
    runs: 0,
    counters: {},
    pending_interrupt_ids: [],
    activity: [],
  };
}

/**
 * Long runs are hard to read, so finished parts fold into one node each (click to unfold):
 *  - the finished stages before the work that is going on (requirements → … → scaffold),
 *  - each wave of parallel tasks once all its tasks are finished ("Wave 2 · 4 tasks"),
 *  - each finished pull request round (follow-up + push).
 * Nothing that is running, waiting or failed is folded, nor anything in `keep`.
 */
export function compactWorkflow(
  nodes: readonly WorkflowNode[],
  edges: readonly WorkflowEdge[],
  keep: ReadonlySet<string> = new Set(),
): CompactResult {
  const byId = new Map(nodes.map((n) => [n.id, n] as const));
  const owner = new Map<string, string>(); // node id -> group id
  const groups = new Map<string, string[]>();
  const groupNodes = new Map<string, WorkflowNode>();
  const foldable = (n: WorkflowNode) => FINISHED.has(n.status) && !keep.has(n.id);
  const add = (id: string, label: string, detail: string, members: WorkflowNode[]) => {
    if (keep.has(id)) {
      return;
    }
    groups.set(id, members.map((m) => m.id));
    groupNodes.set(id, groupNode(id, label, detail, members));
    members.forEach((m) => owner.set(m.id, id));
  };

  // 1. runs of finished stages before the tasks and after them, in flow (rank) order
  const rank = ranks(nodes, edges);
  const isRound = (id: string) => /^(?:followup|push):\d+$/.test(id);
  const taskRanks = nodes.filter((n) => n.kind === 'task').map((n) => rank.get(n.id) ?? 0);
  const firstTask = taskRanks.length ? Math.min(...taskRanks) : Infinity;
  const lastTask = taskRanks.length ? Math.max(...taskRanks) : -Infinity;
  const stages = nodes
    .filter((n) => n.kind !== 'task' && !isRound(n.id))
    .sort((a, b) => (rank.get(a.id) ?? 0) - (rank.get(b.id) ?? 0));
  const span = (members: WorkflowNode[]) =>
    members.length > 2 ? `${members[0].label} → … → ${members[members.length - 1].label}` : members.map((n) => n.label).join(' → ');
  const leading = (segment: WorkflowNode[]) => {
    const run: WorkflowNode[] = [];
    for (const n of segment) {
      if (!foldable(n)) {
        break;
      }
      run.push(n);
    }
    return run;
  };
  const before = leading(stages.filter((n) => (rank.get(n.id) ?? 0) < firstTask));
  if (before.length >= 3) {
    add(`${GROUP_PREFIX}stages`, `${before.length} steps done`, span(before), before);
  }
  const after = leading(stages.filter((n) => (rank.get(n.id) ?? 0) > lastTask));
  if (taskRanks.length && after.length >= 3) {
    add(`${GROUP_PREFIX}checks`, `${after.length} steps done`, span(after), after);
  }

  // 2. waves: task nodes sharing a column (same rank) run in parallel
  const columns = new Map<number, WorkflowNode[]>();
  for (const n of nodes) {
    if (n.kind === 'task') {
      const r = rank.get(n.id) ?? 0;
      columns.set(r, [...(columns.get(r) ?? []), n]);
    }
  }
  [...columns.keys()]
    .sort((a, b) => a - b)
    .forEach((r, index) => {
      const tasks = columns.get(r)!;
      if (tasks.length >= 2 && tasks.every(foldable)) {
        const done = tasks.filter((t) => t.status === 'done').length;
        add(
          `${GROUP_PREFIX}wave:${index + 1}`,
          `Wave ${index + 1} · ${tasks.length} tasks`,
          done === tasks.length ? 'all merged' : `${done} merged, ${tasks.length - done} skipped`,
          tasks,
        );
      }
    });

  // 3. finished pull request rounds
  const rounds = new Map<string, WorkflowNode[]>();
  for (const n of nodes) {
    const m = /^(?:followup|push):(\d+)$/.exec(n.id);
    if (m) {
      rounds.set(m[1], [...(rounds.get(m[1]) ?? []), n]);
    }
  }
  for (const [round, members] of rounds) {
    if (members.every(foldable)) {
      add(`${GROUP_PREFIX}round:${round}`, `PR round ${round}`, members.map((n) => n.detail || n.label).join(' · '), members);
    }
  }

  if (!groups.size) {
    return { nodes: [...nodes], edges: [...edges], groups };
  }
  const outNodes: WorkflowNode[] = [];
  const placed = new Set<string>();
  for (const n of nodes) {
    const g = owner.get(n.id);
    if (!g) {
      outNodes.push(n);
    } else if (!placed.has(g)) {
      placed.add(g);
      outNodes.push(groupNodes.get(g)!);
    }
  }
  const outEdges: WorkflowEdge[] = [];
  const seen = new Set<string>();
  for (const e of edges) {
    const source = owner.get(e.source) ?? e.source;
    const target = owner.get(e.target) ?? e.target;
    if (source === target || !byId.has(e.source) || !byId.has(e.target)) {
      continue;
    }
    const folded = source !== e.source || target !== e.target;
    const id = folded ? `${source}->${target}:${e.kind}` : e.id;
    if (seen.has(id)) {
      continue;
    }
    seen.add(id);
    outEdges.push(folded ? { ...e, id, source, target, label: e.kind === 'loop' ? e.label : null } : e);
  }
  return { nodes: outNodes, edges: outEdges, groups };
}
