import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  computed,
  NgZone,
  effect,
  inject,
  input,
  output,
  signal,
  untracked,
  viewChild,
} from '@angular/core';
import { Edge, Node, Vflow, VflowComponent } from 'ngx-vflow';

import { TERMINAL_STATUSES, Workflow, WorkflowEdge, WorkflowNode } from '../../core/api.models';
import {
  iconKind,
  layoutWorkflow,
  nodeElapsed,
  nodeSize,
  structureKey,
} from '../../core/workflow-layout';
import { AgentIconComponent } from '../../shared/agent-icon.component';

const STATUS_LABEL: Record<string, string> = {
  pending: 'queued',
  running: 'working',
  waiting: 'needs you',
  done: 'done',
  failed: 'failed',
  skipped: 'skipped',
};

/**
 * The run as an n8n-style flow: stages and one node per task, laid out left to right. Node
 * content is kept in signals, so live updates re-render nodes in place; the layout is only
 * recomputed when nodes or edges are added or removed (e.g. once the plan exists).
 */
@Component({
  selector: 'app-workflow-graph',
  imports: [Vflow, AgentIconComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="canvas">
      <div class="tools">
        <button type="button" [class.on]="following()" (click)="setFollow(true)"
                title="Zoom to the steps that are running or waiting for you">Follow</button>
        <button type="button" [class.on]="!following()" (click)="setFollow(false)"
                title="Show the whole workflow">Overview</button>
      </div>
      <vflow view="auto" [nodes]="nodes()" [edges]="edges()" [minZoom]="0.2" [maxZoom]="1.2"
             [background]="{ type: 'dots', gap: 22, size: 1, color: 'rgba(94, 200, 255, 0.16)', backgroundColor: 'transparent' }">
        <ng-template let-ctx nodeHtml>
          @let n = ctx.data();
          <div class="node" [class]="'node ' + n.kind + ' ' + n.status" [class.selected]="n.id === selected()"
               (click)="nodeSelected.emit(n.id)" (keydown.enter)="nodeSelected.emit(n.id)"
               tabindex="0" role="button" [attr.aria-label]="n.label + ': ' + statusLabel(n.status)"
               [attr.data-node]="n.id">
            <handle type="target" position="left" />
            <div class="top">
              <app-agent-icon [kind]="icon(n)" [size]="n.kind === 'task' ? 38 : 34"
                              [active]="n.status === 'running'" />
              <div class="titles">
                <div class="label" [title]="n.label">{{ n.label }}</div>
                <div class="status"><span class="dot"></span>{{ statusLabel(n.status) }}
                  @if (elapsed(n); as t) { <span class="time">· {{ t }}</span> }
                </div>
              </div>
              @if (n.pending_interrupt_ids.length && n.status === 'waiting') { <span class="badge" title="Waiting for you">!</span> }
            </div>
            @if (n.detail) { <div class="detail" [title]="n.detail">{{ n.detail }}</div> }
            @if (n.kind === 'task') {
              <div class="steps">
                @for (s of taskSteps; track s.key) {
                  <span class="step" [class.on]="n.status === 'running' && n.step === s.key"
                        [class.passed]="stepPassed(n, s.key)">{{ s.label }}</span>
                }
                @if (n.counters['coordinator_actions']) {
                  <span class="coord" title="Coordinator actions">⚑ {{ n.counters['coordinator_actions'] }}</span>
                }
              </div>
            }
            <handle type="source" position="right" />
          </div>
        </ng-template>

        <ng-template let-ctx edge>
          <svg:path class="edge-glow" [class.active]="ctx.data()?.active" [attr.d]="ctx.path()" />
          <svg:path class="edge" [class.active]="ctx.data()?.active" [class.loop]="ctx.data()?.kind === 'loop'"
                    [attr.d]="ctx.path()" [attr.marker-end]="ctx.markerEnd()" />
        </ng-template>

        <ng-template let-ctx edgeLabelHtml>
          <span class="edge-label">{{ ctx.label.data?.text }}</span>
        </ng-template>
      </vflow>
    </div>
  `,
  styles: `
    :host { display: block; }
    .canvas { position: relative; height: 100%; min-height: 360px; border-radius: var(--dc-radius); overflow: hidden;
              border: 1px solid var(--dc-border);
              background: radial-gradient(800px 300px at 50% 0%, rgba(62, 230, 255, 0.07), transparent 70%), rgba(6, 12, 24, 0.85); }
    .tools { position: absolute; z-index: 2; top: 10px; right: 10px; display: flex; gap: 4px; padding: 3px;
             border-radius: 10px; background: rgba(6, 12, 24, 0.85); border: 1px solid var(--dc-border); }
    .tools button { font: inherit; font-size: 12px; cursor: pointer; border: 0; border-radius: 7px; padding: 4px 10px;
                    color: var(--dc-text-dim); background: transparent; }
    .tools button.on { color: #04121c; background: var(--dc-cyan); box-shadow: var(--dc-glow-cyan); }
    .node { box-sizing: border-box; width: 100%; height: 100%; padding: 9px 11px; cursor: pointer;
            border-radius: 12px; color: var(--dc-text); background: rgba(12, 22, 40, 0.92);
            border: 1px solid var(--dc-border); display: flex; flex-direction: column; gap: 5px;
            transition: border-color 0.2s, box-shadow 0.2s; outline: none; }
    .node:hover, .node:focus-visible { border-color: var(--dc-border-strong); }
    .node.selected { border-color: var(--dc-cyan); box-shadow: var(--dc-glow-cyan); }
    .node.running { border-color: rgba(62, 230, 255, 0.7); animation: dc-pulse 1.8s ease-in-out infinite; }
    .node.waiting { border-color: var(--dc-amber); animation: dc-pulse-amber 1.6s ease-in-out infinite; }
    .node.done { border-color: rgba(45, 226, 176, 0.45); }
    .node.failed { border-color: var(--dc-red); box-shadow: var(--dc-glow-red); }
    .node.skipped, .node.pending { opacity: 0.62; }
    .node.skipped { background: repeating-linear-gradient(135deg, rgba(12, 22, 40, 0.92) 0 8px, rgba(20, 32, 54, 0.92) 8px 16px); }
    .top { display: flex; align-items: center; gap: 9px; position: relative; }
    .titles { min-width: 0; flex: 1; padding-right: 14px; }
    .label { font-weight: 600; font-size: 13px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; color: #f1f7ff; }
    .status { font-size: 11px; color: var(--dc-text-dim); display: flex; align-items: center; gap: 5px; }
    .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--dc-text-faint); }
    .running .dot { background: var(--dc-cyan); box-shadow: 0 0 8px var(--dc-cyan); }
    .waiting .dot { background: var(--dc-amber); box-shadow: 0 0 8px var(--dc-amber); }
    .done .dot { background: var(--dc-teal); box-shadow: 0 0 6px var(--dc-teal); }
    .failed .dot { background: var(--dc-red); box-shadow: 0 0 8px var(--dc-red); }
    .time { color: var(--dc-text-faint); }
    .badge { position: absolute; top: -4px; right: -4px; width: 18px; height: 18px; border-radius: 50%;
             background: var(--dc-amber); color: #231600; font-weight: 800; font-size: 12px;
             display: grid; place-items: center; box-shadow: var(--dc-glow-amber); }
    .detail { font-size: 11.5px; color: var(--dc-text-dim); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .steps { display: flex; gap: 4px; align-items: center; margin-top: auto; }
    .step { font-size: 10px; padding: 1px 7px; border-radius: 9px; color: var(--dc-text-faint);
            border: 1px solid rgba(94, 200, 255, 0.14); }
    .step.passed { color: var(--dc-teal); border-color: rgba(45, 226, 176, 0.35); }
    .step.on { color: #04121c; background: var(--dc-cyan); border-color: var(--dc-cyan); box-shadow: var(--dc-glow-cyan); }
    .coord { font-size: 10px; color: #ff7ad9; margin-left: auto; }
    .edge-glow { fill: none; stroke: transparent; stroke-width: 7; }
    .edge-glow.active { stroke: rgba(62, 230, 255, 0.18); filter: blur(2px); }
    .edge { fill: none; stroke: rgba(120, 160, 210, 0.45); stroke-width: 1.6; }
    .edge.active { stroke: var(--dc-cyan); stroke-width: 2.2; stroke-dasharray: 8 4; animation: dc-flow 0.8s linear infinite; }
    .edge.loop { stroke: var(--dc-amber); stroke-dasharray: 4 4; opacity: 0.8; }
    .edge-label { font-size: 10.5px; padding: 1px 7px; border-radius: 8px; color: var(--dc-amber);
                  background: rgba(20, 14, 0, 0.85); border: 1px solid rgba(255, 193, 77, 0.4); white-space: nowrap; }
  `,
})
export class WorkflowGraphComponent {
  readonly workflow = input.required<Workflow>();
  readonly selected = input<string | null>(null);
  readonly nodeSelected = output<string>();

  readonly nodes = signal<Node<WorkflowNode>[]>([]);
  readonly edges = signal<Edge<WorkflowEdge>[]>([]);
  /** Ticks every second while something is active, for the elapsed-time labels. */
  readonly now = signal(Date.now());

  readonly taskSteps = [
    { key: 'developer', label: 'dev' },
    { key: 'reviewer', label: 'review' },
    { key: 'qa', label: 'QA' },
  ];

  private readonly flow = viewChild(VflowComponent);
  private structure = '';
  /** Follow the active work (default) or show the whole flow. */
  readonly follow = signal(true);
  /** Effective mode: a finished run is always shown as a whole. */
  readonly following = computed(() => this.follow() && !TERMINAL_STATUSES.includes(this.workflow().status));
  private focusKey = '';
  private readonly data = new Map<string, ReturnType<typeof signal<WorkflowNode>>>();
  private readonly edgeData = new Map<string, ReturnType<typeof signal<WorkflowEdge>>>();

  constructor() {
    effect(() => {
      const wf = this.workflow();
      untracked(() => this.sync(wf));
    });
    // Outside the zone, so the ticking never keeps the app "unstable"; the signal update still
    // schedules change detection for this OnPush component.
    const timer = inject(NgZone).runOutsideAngular(() =>
      setInterval(() => {
        if (this.workflow().nodes.some((n) => n.status === 'running' || n.status === 'waiting')) {
          this.now.set(Date.now());
        }
      }, 1000),
    );
    inject(DestroyRef).onDestroy(() => clearInterval(timer));
  }

  icon = iconKind;
  statusLabel = (status: string): string => STATUS_LABEL[status] ?? status;
  elapsed = (node: WorkflowNode): string => nodeElapsed(node, this.now());

  stepPassed(node: WorkflowNode, step: string): boolean {
    if (node.status === 'done') {
      return true;
    }
    const order = this.taskSteps.map((s) => s.key);
    const current = order.indexOf(node.step ?? '');
    return current > order.indexOf(step);
  }

  setFollow(on: boolean): void {
    this.follow.set(on);
    this.focusKey = '';
    this.reframe(this.workflow(), true);
  }

  /** Move the viewport when the set of active nodes changes (or when asked to). */
  private reframe(wf: Workflow, force = false): void {
    const ids = focusNodes(wf);
    const follow = this.follow() && !TERMINAL_STATUSES.includes(wf.status);
    const key = follow ? ids.join('|') : 'overview';
    if (!force && key === this.focusKey) {
      return;
    }
    this.focusKey = key;
    // after the (re)rendered nodes have been measured
    setTimeout(() =>
      this.flow()?.fitView(
        follow && ids.length
          ? { nodes: ids, padding: 0.35, duration: 400 }
          : { padding: 0.08, duration: 400 },
      ),
    );
  }

  /** Re-render in place when only content changed; rebuild + re-layout on structural change. */
  private sync(wf: Workflow): void {
    const key = structureKey(wf.nodes, wf.edges);
    if (key === this.structure) {
      for (const n of wf.nodes) {
        this.data.get(n.id)?.set(n);
      }
      for (const e of wf.edges) {
        this.edgeData.get(e.id)?.set(e);
      }
      this.reframe(wf);
      return;
    }
    this.structure = key;
    const positions = layoutWorkflow(wf.nodes, wf.edges);
    this.data.clear();
    this.edgeData.clear();
    this.nodes.set(
      wf.nodes.map((n) => {
        const size = nodeSize(n);
        const data = signal(n);
        this.data.set(n.id, data);
        return {
          id: n.id,
          type: 'html-template',
          point: signal(positions.get(n.id) ?? { x: 0, y: 0 }),
          width: signal(size.width),
          height: signal(size.height),
          data,
        };
      }),
    );
    this.edges.set(
      wf.edges.map((e) => {
        const data = signal(e);
        this.edgeData.set(e.id, data);
        return {
          id: e.id,
          source: e.source,
          target: e.target,
          type: 'template',
          curve: signal(e.kind === 'loop' ? 'bezier' : 'smooth-step'),
          data,
          markers: signal({
            end: { type: 'arrow-closed', color: e.kind === 'loop' ? '#ffc14d' : '#6f93bf', width: 14, height: 14 },
          }),
          edgeLabels: signal(
            e.label ? { center: { type: 'html-template', data: { text: e.label } } } : {},
          ),
        } satisfies Edge<WorkflowEdge>;
      }),
    );
    this.reframe(wf, true);
  }
}

/**
 * Nodes to frame in follow mode: everything running or waiting plus its direct neighbors; if
 * nothing is active, the last finished node and what comes next.
 */
export function focusNodes(wf: Workflow): string[] {
  let core = wf.nodes.filter((n) => n.status === 'running' || n.status === 'waiting').map((n) => n.id);
  if (!core.length) {
    const done = wf.nodes.filter((n) => n.status === 'done');
    core = done.length ? [done[done.length - 1].id] : wf.nodes.slice(0, 1).map((n) => n.id);
  }
  const set = new Set(core);
  for (const e of wf.edges) {
    if (e.kind !== 'flow') {
      continue;
    }
    if (core.includes(e.source)) {
      set.add(e.target);
    }
    if (core.includes(e.target)) {
      set.add(e.source);
    }
  }
  return wf.nodes.map((n) => n.id).filter((id) => set.has(id));
}
