// Positions a DevCrew run's workflow graph (WorkflowNode[] + WorkflowEdge[],
// as returned by GET /runs/:id/workflow) for the n8n-style canvas: a
// left-to-right layered DAG layout computed purely from "flow" edges.
// "loop" edges (retry backlinks, e.g. a rejected plan going back to the
// Planner) are excluded here -- including them would create cycles in the
// layering pass -- and are instead drawn separately by the canvas as
// curved backlinks over the positions this produces.
//
// Long sequential runs (a dozen-plus stages) are wrapped into row-bands --
// at most `maxColumnsPerBand` DAG-columns per band -- instead of one very
// long single-row strip. A whole DAG-column (including any stacked
// parallel nodes) always moves as one atomic unit into whichever band it
// falls in; it is never split across two bands. Every band runs
// left-to-right from x=0 (never alternating/boustrophedon), matching how a
// typical n8n-style canvas wraps. The canvas component detects a
// band-crossing edge positionally (`from.band !== to.band`) and routes it
// with its own elbow connector instead of the normal same-row curve.

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
  maxColumnsPerBand: number;
  bandGap: number;
}

export const DEFAULT_DIMS: LayoutDims = {
  nodeWidth: NODE_WIDTH,
  nodeHeight: NODE_HEIGHT,
  colGap: COL_GAP,
  rowGap: ROW_GAP,
  maxColumnsPerBand: 3,
  bandGap: 90,
};

// Compact view mode: smaller cards, tighter gaps -- lets a wide run fit in
// less horizontal scroll without changing the layout algorithm itself.
// Smaller cards legibly fit more per row before wrapping is needed.
export const COMPACT_DIMS: LayoutDims = {
  nodeWidth: 120,
  nodeHeight: 44,
  colGap: 56,
  rowGap: 12,
  maxColumnsPerBand: 5,
  bandGap: 56,
};

export interface PositionedNode {
  node: WorkflowNode;
  x: number;
  y: number;
  band: number;
}

export interface WorkflowLayout {
  positioned: PositionedNode[];
  width: number;
  height: number;
}

// Splits `columnCount` columns into `numBands` contiguous, evenly-sized
// chunks (e.g. 7 columns / 3 bands -> [3, 2, 2]) so no trailing band ends
// up lopsided.
function bandSizes(columnCount: number, numBands: number): number[] {
  const base = Math.floor(columnCount / numBands);
  const remainder = columnCount % numBands;
  return Array.from({ length: numBands }, (_, i) => base + (i < remainder ? 1 : 0));
}

export function layoutWorkflow(
  nodes: WorkflowNode[],
  edges: WorkflowEdge[],
  dims: LayoutDims = DEFAULT_DIMS
): WorkflowLayout {
  const { nodeWidth, nodeHeight, colGap, rowGap, maxColumnsPerBand, bandGap } = dims;
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

  const columnCount = maxColumn + 1;
  const numBands = Math.max(1, Math.ceil(columnCount / maxColumnsPerBand));
  const sizes = bandSizes(columnCount, numBands);
  const bandStartColumn: number[] = [];
  {
    let col = 0;
    sizes.forEach((size, b) => {
      bandStartColumn[b] = col;
      col += size;
    });
  }

  const columnHeight = (count: number) => count * nodeHeight + Math.max(0, count - 1) * rowGap;

  let yCursor = 0;
  let maxBandWidth = 0;
  const positioned: PositionedNode[] = [];
  for (let b = 0; b < numBands; b++) {
    const size = sizes[b];
    const cols = Array.from({ length: size }, (_, i) => bandStartColumn[b] + i);
    const bandMaxRows = Math.max(1, ...cols.map((c) => (byColumn.get(c) ?? []).length));
    const thisBandHeight = columnHeight(bandMaxRows);
    const bandWidth = size * nodeWidth + Math.max(0, size - 1) * colGap;
    maxBandWidth = Math.max(maxBandWidth, bandWidth);

    cols.forEach((c, colInBand) => {
      const colNodes = byColumn.get(c) ?? [];
      const thisColHeight = columnHeight(colNodes.length);
      const offsetY = (thisBandHeight - thisColHeight) / 2;
      colNodes.forEach((node, row) => {
        positioned.push({
          node,
          band: b,
          x: colInBand * (nodeWidth + colGap),
          y: yCursor + offsetY + row * (nodeHeight + rowGap),
        });
      });
    });

    yCursor += thisBandHeight + (b < numBands - 1 ? bandGap : 0);
  }

  return {
    positioned,
    width: maxBandWidth,
    height: yCursor,
  };
}
