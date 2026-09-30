import { CommonModule } from '@angular/common';
import {
  AfterViewInit,
  Component,
  ElementRef,
  EventEmitter,
  Input,
  OnChanges,
  Output,
  SimpleChanges,
  ViewChild,
} from '@angular/core';

import { WorkflowEdge, WorkflowNode } from '../../../../services/devcrew.service';
import { humanizeStatus } from '../../../../services/format-status.util';
import {
  COMPACT_DIMS,
  DEFAULT_DIMS,
  LayoutDims,
  PositionedNode,
  layoutWorkflow,
} from '../../../../services/workflow-layout.util';

const LOOP_DROP = 40;
const CANVAS_PADDING = 24;
const MINIMAP_WIDTH = 160;
const MINIMAP_HEIGHT = 110;

export type CanvasViewMode = 'follow' | 'overview' | 'compact';

interface FlowEdgePath {
  id: string;
  d: string;
  active: boolean;
}

interface LoopEdgePath {
  id: string;
  d: string;
  label: string | null;
  labelX: number;
  labelY: number;
}

interface CanvasRender {
  positioned: PositionedNode[];
  width: number;
  height: number;
  flowPaths: FlowEdgePath[];
  loopPaths: LoopEdgePath[];
}

interface MinimapRect {
  id: string;
  x: number;
  y: number;
  w: number;
  h: number;
  status: string;
}

interface ViewportRect {
  x: number;
  y: number;
  w: number;
  h: number;
}

interface DragState {
  id: string;
  pointerId: number;
  startClientX: number;
  startClientY: number;
  originX: number;
  originY: number;
}

@Component({
  selector: 'app-workflow-canvas',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './workflow-canvas.component.html',
  styleUrl: './workflow-canvas.component.css',
})
export class WorkflowCanvasComponent implements OnChanges, AfterViewInit {
  @Input() runId = '';
  @Input() nodes: WorkflowNode[] = [];
  @Input() edges: WorkflowEdge[] = [];
  @Input() attention: string[] = [];
  @Input() selectedId: string | null = null;
  @Output() select = new EventEmitter<string>();

  @Input() viewMode: CanvasViewMode = 'follow';
  @Output() viewModeChange = new EventEmitter<CanvasViewMode>();
  @Input() showMinimap = false;
  @Output() showMinimapChange = new EventEmitter<boolean>();

  @ViewChild('scrollEl') scrollElRef?: ElementRef<HTMLDivElement>;

  readonly canvasPadding = CANVAS_PADDING;
  readonly minimapWidth = MINIMAP_WIDTH;
  readonly minimapHeight = MINIMAP_HEIGHT;
  readonly humanizeStatus = humanizeStatus;
  readonly modes: CanvasViewMode[] = ['follow', 'overview', 'compact'];

  // Positions the user has dragged a node to, keyed by node id, for the
  // *current* runId + viewMode. Follow/Compact use different LayoutDims
  // (different node size/column gaps), so a raw pixel position saved in one
  // mode is meaningless in another -- each mode gets its own saved set (see
  // storageKey()) to avoid a node jumping to a nonsensical spot when the
  // view-mode changes.
  private dragOverrides = new Map<string, { x: number; y: number }>();
  private dragState: DragState | null = null;
  private dragMoved = false;

  get dims(): LayoutDims {
    return this.viewMode === 'compact' ? COMPACT_DIMS : DEFAULT_DIMS;
  }

  get render(): CanvasRender {
    const dims = this.dims;
    const layout = layoutWorkflow(this.nodes, this.edges, dims);
    const positioned: PositionedNode[] = layout.positioned.map((p) => {
      const override = this.dragOverrides.get(p.node.id);
      return override ? { ...p, x: override.x, y: override.y } : p;
    });
    const byId = new Map(positioned.map((p) => [p.node.id, p]));

    const flowPaths: FlowEdgePath[] = [];
    const loopPaths: LoopEdgePath[] = [];
    for (const edge of this.edges) {
      const from = byId.get(edge.source);
      const to = byId.get(edge.target);
      if (!from || !to) {
        continue;
      }
      if (edge.kind === 'loop') {
        loopPaths.push(this.buildLoopPath(edge, from, to, dims));
      } else {
        flowPaths.push(this.buildFlowPath(edge, from, to, dims));
      }
    }

    // A dragged node can sit past the auto-computed layout's edge -- grow
    // the canvas bounds to keep it reachable by scroll instead of clipped.
    let maxX = layout.width;
    let maxY = layout.height;
    for (const p of positioned) {
      maxX = Math.max(maxX, p.x + dims.nodeWidth);
      maxY = Math.max(maxY, p.y + dims.nodeHeight);
    }

    return {
      positioned,
      width: maxX + CANVAS_PADDING * 2,
      height: maxY + CANVAS_PADDING * 2,
      flowPaths,
      loopPaths,
    };
  }

  get fitScale(): number {
    const el = this.scrollElRef?.nativeElement;
    if (!el || this.viewMode !== 'overview') {
      return 1;
    }
    const { width, height } = this.render;
    if (!width || !height) {
      return 1;
    }
    return Math.min(1, el.clientWidth / width, el.clientHeight / height);
  }

  get miniScale(): number {
    const { width, height } = this.render;
    if (!width || !height) {
      return 1;
    }
    return Math.min(this.minimapWidth / width, this.minimapHeight / height);
  }

  get minimapRects(): MinimapRect[] {
    const scale = this.miniScale;
    const dims = this.dims;
    return this.render.positioned.map((p) => ({
      id: p.node.id,
      x: (p.x + this.canvasPadding) * scale,
      y: (p.y + this.canvasPadding) * scale,
      w: Math.max(2, dims.nodeWidth * scale),
      h: Math.max(2, dims.nodeHeight * scale),
      status: p.node.status,
    }));
  }

  get minimapViewport(): ViewportRect | null {
    const el = this.scrollElRef?.nativeElement;
    if (!el) {
      return null;
    }
    const scale = this.miniScale;
    return {
      x: el.scrollLeft * scale,
      y: el.scrollTop * scale,
      w: Math.min(this.minimapWidth, el.clientWidth * scale),
      h: Math.min(this.minimapHeight, el.clientHeight * scale),
    };
  }

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['runId']) {
      this.loadOverrides();
    }
    if ((changes['selectedId'] || changes['viewMode']) && this.viewMode === 'follow') {
      this.scrollToSelected();
    }
  }

  ngAfterViewInit(): void {
    if (this.viewMode === 'follow') {
      this.scrollToSelected();
    }
  }

  private scrollToSelected(): void {
    const el = this.scrollElRef?.nativeElement;
    const id = this.selectedId;
    if (!el || !id) {
      return;
    }
    const pos = this.render.positioned.find((p) => p.node.id === id);
    if (!pos) {
      return;
    }
    const dims = this.dims;
    const targetLeft = pos.x + this.canvasPadding - (el.clientWidth - dims.nodeWidth) / 2;
    const targetTop = pos.y + this.canvasPadding - (el.clientHeight - dims.nodeHeight) / 2;
    el.scrollLeft = Math.max(0, Math.min(targetLeft, el.scrollWidth - el.clientWidth));
    el.scrollTop = Math.max(0, Math.min(targetTop, el.scrollHeight - el.clientHeight));
  }

  private buildFlowPath(
    edge: WorkflowEdge,
    from: PositionedNode,
    to: PositionedNode,
    dims: LayoutDims
  ): FlowEdgePath {
    const sx = from.x + dims.nodeWidth + CANVAS_PADDING;
    const sy = from.y + dims.nodeHeight / 2 + CANVAS_PADDING;
    const tx = to.x + CANVAS_PADDING;
    const ty = to.y + dims.nodeHeight / 2 + CANVAS_PADDING;
    const midX = (sx + tx) / 2;
    return {
      id: edge.id,
      d: `M ${sx},${sy} C ${midX},${sy} ${midX},${ty} ${tx},${ty}`,
      active: edge.active,
    };
  }

  private buildLoopPath(
    edge: WorkflowEdge,
    from: PositionedNode,
    to: PositionedNode,
    dims: LayoutDims
  ): LoopEdgePath {
    const sx = from.x + dims.nodeWidth / 2 + CANVAS_PADDING;
    const sy = from.y + dims.nodeHeight + CANVAS_PADDING;
    const tx = to.x + dims.nodeWidth / 2 + CANVAS_PADDING;
    const ty = to.y + dims.nodeHeight + CANVAS_PADDING;
    const dropY = Math.max(sy, ty) + LOOP_DROP;
    return {
      id: edge.id,
      d: `M ${sx},${sy} C ${sx},${dropY} ${tx},${dropY} ${tx},${ty}`,
      label: edge.label,
      labelX: (sx + tx) / 2,
      labelY: dropY,
    };
  }

  onSelect(id: string): void {
    this.select.emit(id);
  }

  setViewMode(mode: CanvasViewMode): void {
    if (this.viewMode === mode) {
      return;
    }
    this.viewMode = mode;
    this.loadOverrides();
    this.viewModeChange.emit(mode);
    if (mode === 'follow') {
      // Deferred: the (possibly compact-dims) layout needs one change
      // detection cycle to settle before positions used for scrolling are current.
      setTimeout(() => this.scrollToSelected());
    }
  }

  get hasDragOverrides(): boolean {
    return this.dragOverrides.size > 0;
  }

  resetLayout(): void {
    this.dragOverrides = new Map();
    if (this.runId && typeof localStorage !== 'undefined') {
      localStorage.removeItem(this.storageKey());
    }
  }

  private storageKey(): string {
    return `dc-node-pos:${this.runId}:${this.viewMode}`;
  }

  private loadOverrides(): void {
    this.dragOverrides = new Map();
    if (!this.runId || typeof localStorage === 'undefined') {
      return;
    }
    try {
      const raw = localStorage.getItem(this.storageKey());
      if (!raw) {
        return;
      }
      const parsed = JSON.parse(raw) as Record<string, { x: number; y: number }>;
      for (const [id, pos] of Object.entries(parsed)) {
        this.dragOverrides.set(id, pos);
      }
    } catch {
      // Corrupted localStorage entry -- fall back to the auto-computed layout.
    }
  }

  private saveOverrides(): void {
    if (!this.runId || typeof localStorage === 'undefined') {
      return;
    }
    if (!this.dragOverrides.size) {
      localStorage.removeItem(this.storageKey());
      return;
    }
    const obj: Record<string, { x: number; y: number }> = {};
    for (const [id, pos] of this.dragOverrides) {
      obj[id] = pos;
    }
    localStorage.setItem(this.storageKey(), JSON.stringify(obj));
  }

  onNodePointerDown(event: PointerEvent, p: PositionedNode): void {
    if (event.button !== 0) {
      return;
    }
    (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
    this.dragMoved = false;
    this.dragState = {
      id: p.node.id,
      pointerId: event.pointerId,
      startClientX: event.clientX,
      startClientY: event.clientY,
      originX: p.x,
      originY: p.y,
    };
  }

  onNodePointerMove(event: PointerEvent): void {
    const state = this.dragState;
    if (!state || event.pointerId !== state.pointerId) {
      return;
    }
    const dxScreen = event.clientX - state.startClientX;
    const dyScreen = event.clientY - state.startClientY;
    if (!this.dragMoved && Math.hypot(dxScreen, dyScreen) > 4) {
      this.dragMoved = true;
    }
    // .dc-canvas-inner is CSS-scaled in Overview mode, so screen-pixel
    // pointer deltas need to be converted back to logical (pre-scale)
    // coordinates -- otherwise dragging feels far too fast/slow depending
    // on zoom level. Compact mode has no transform (it only shrinks
    // `dims`), so no correction is needed there.
    const scale = this.viewMode === 'overview' ? this.fitScale || 1 : 1;
    const nextX = Math.max(0, state.originX + dxScreen / scale);
    const nextY = Math.max(0, state.originY + dyScreen / scale);
    this.dragOverrides.set(state.id, { x: nextX, y: nextY });
  }

  onNodePointerUp(event: PointerEvent, nodeId: string): void {
    const state = this.dragState;
    if (!state || event.pointerId !== state.pointerId) {
      return;
    }
    this.dragState = null;
    if (this.dragMoved) {
      this.saveOverrides();
    } else {
      this.onSelect(nodeId);
    }
  }

  onNodePointerCancel(event: PointerEvent): void {
    const state = this.dragState;
    if (!state || event.pointerId !== state.pointerId) {
      return;
    }
    this.dragState = null;
    if (this.dragMoved) {
      this.saveOverrides();
    }
  }

  toggleMinimap(): void {
    this.showMinimap = !this.showMinimap;
    this.showMinimapChange.emit(this.showMinimap);
  }

  onScroll(): void {
    // Intentionally empty: a bound (scroll) listener guarantees change
    // detection re-reads scrollLeft/scrollTop for the minimap viewport
    // rect and (in overview mode) nothing else depends on scroll position,
    // so there's nothing else to do here.
  }

  onMinimapClick(event: MouseEvent): void {
    const el = this.scrollElRef?.nativeElement;
    const svg = event.currentTarget as SVGSVGElement;
    if (!el || !svg) {
      return;
    }
    const rect = svg.getBoundingClientRect();
    const scale = this.miniScale || 1;
    const clickX = event.clientX - rect.left;
    const clickY = event.clientY - rect.top;
    el.scrollLeft = Math.max(0, clickX / scale - el.clientWidth / 2);
    el.scrollTop = Math.max(0, clickY / scale - el.clientHeight / 2);
  }
}
