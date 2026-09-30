// Positions a DevCrew run's workflow graph (WorkflowNode[] + WorkflowEdge[],
// as returned by GET /runs/:id/workflow) for the n8n-style canvas: a
// left-to-right layered DAG layout computed purely from "flow" edges.
// "loop" edges (retry backlinks, e.g. a rejected plan going back to the
// Planner) are excluded here -- including them would create cycles in the
// layering pass -- and are instead drawn separately by the canvas as
// curved backlinks over the positions this produces.

import { WorkflowEdge, WorkflowNode } from './devcrew.service';

export const NODE_WIDTH = 200;
export const NODE_HEIGHT = 72;
export const COL_GAP = 96;
export const ROW_GAP = 20;

export interface LayoutDims {
  nodeWidth: number;
  nodeHeight: number;
  colGap: number;
  rowGap: number;
}

export const DEFAULT_DIMS: LayoutDims = {
  nodeWidth: NODE_WIDTH,
  nodeHeight: NODE_HEIGHT,
  colGap: COL_GAP,
  rowGap: ROW_GAP,
};

// Compact view mode: smaller cards, tighter gaps -- lets a wide run fit in
// less horizontal scroll without changing the layout algorithm itself.
export const COMPACT_DIMS: LayoutDims = {
  nodeWidth: 120,
  nodeHeight: 44,
  colGap: 56,
  rowGap: 12,
};

export interface PositionedNode {
  node: WorkflowNode;
  x: number;
  y: number;
}

export interface WorkflowLayout {
  positioned: PositionedNode[];
  width: number;
  height: number;
}

export function layoutWorkflow(
  nodes: WorkflowNode[],
  edges: WorkflowEdge[],
  dims: LayoutDims = DEFAULT_DIMS
): WorkflowLayout {
  const { nodeWidth, nodeHeight, colGap, rowGap } = dims;
  const flowEdges = edges.filter((e) => e.kind !== 'loop');
  const predecessors = new Map<string, string[]>();
  nodes.forEach((n) => predecessors.set(n.id, []));
  for (const edge of flowEdges) {
    if (predecessors.has(edge.target) && predecessors.has(edge.source)) {
      predecessors.get(edge.target)!.push(edge.source);
    }
  }

  const column = new Map<string, number>();
  const visiting = new Set<string>();
  function rank(id: string): number {
    if (column.has(id)) {
      return column.get(id)!;
    }
    if (visiting.has(id)) {
      return 0; // defensive cycle guard; the backend's flow edges shouldn't ever cycle
    }
    visiting.add(id);
    const preds = predecessors.get(id) ?? [];
    const r = preds.length ? Math.max(...preds.map(rank)) + 1 : 0;
    visiting.delete(id);
    column.set(id, r);
    return r;
  }
  nodes.forEach((n) => rank(n.id));

  const byColumn = new Map<number, WorkflowNode[]>();
  let maxColumn = 0;
  for (const n of nodes) {
    const c = column.get(n.id) ?? 0;
    maxColumn = Math.max(maxColumn, c);
    if (!byColumn.has(c)) {
      byColumn.set(c, []);
    }
    byColumn.get(c)!.push(n);
  }

  const maxRows = Math.max(1, ...Array.from(byColumn.values()).map((col) => col.length));
  const columnHeight = (count: number) => count * nodeHeight + (count - 1) * rowGap;
  const canvasHeight = columnHeight(maxRows);

  const positioned: PositionedNode[] = [];
  for (const [c, colNodes] of byColumn) {
    const thisHeight = columnHeight(colNodes.length);
    const offsetY = (canvasHeight - thisHeight) / 2;
    colNodes.forEach((node, row) => {
      positioned.push({
        node,
        x: c * (nodeWidth + colGap),
        y: offsetY + row * (nodeHeight + rowGap),
      });
    });
  }

  return {
    positioned,
    width: (maxColumn + 1) * nodeWidth + maxColumn * colGap,
    height: canvasHeight,
  };
}
