import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  computed,
  effect,
  inject,
  input,
  signal,
  untracked,
} from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatProgressBarModule } from '@angular/material/progress-bar';
import { MatTabsModule } from '@angular/material/tabs';
import { RouterLink } from '@angular/router';
import { forkJoin } from 'rxjs';

import {
  ResumeRequest,
  RunDetail,
  RunEvent,
  TERMINAL_STATUSES,
  Workflow,
  WorkflowNode,
} from '../../core/api.models';
import { ApiService } from '../../core/api.service';
import { requestTitle } from '../../core/requirements';
import { RunEventStream, RunEventsService } from '../../core/run-events.service';
import { StatusChipComponent } from '../../shared/status-chip.component';
import { DesignViewComponent } from './design-view.component';
import { EventTimelineComponent } from './event-timeline.component';
import { FileExplorerComponent } from './file-explorer.component';
import { PlanViewComponent } from './plan-view.component';
import { QaPanelComponent } from './qa-panel.component';
import { WorkflowGraphComponent } from './workflow-graph.component';
import { WorkflowPanelComponent } from './workflow-panel.component';

/** Every event can change the workflow view; re-fetch at most every REFRESH_DEBOUNCE_MS. */
const REFRESH_DEBOUNCE_MS = 300;

@Component({
  selector: 'app-run-detail',
  imports: [
    RouterLink, MatTabsModule, MatButtonModule, MatProgressBarModule, StatusChipComponent,
    EventTimelineComponent, PlanViewComponent, DesignViewComponent, QaPanelComponent,
    FileExplorerComponent, WorkflowGraphComponent, WorkflowPanelComponent,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <a routerLink="/" class="back">← Runs</a>
    @if (error(); as e) {
      <p class="error" role="alert">{{ e }}</p>
    }
    @if (run(); as run) {
      <header class="head">
        <div>
          <h1>{{ title() }}</h1>
          <div class="meta">
            <app-status-chip [status]="run.status" />
            <span>{{ run.repo_target }}</span>
            @if (run.target === 'existing') {
              <span class="badge">existing repo · {{ run.mode === 'quick' ? 'quick fix' : 'full' }}
                @if (run.base_branch) { · base {{ run.base_branch }} }</span>
            }
            @if (run.issue; as issue) {
              <a class="badge issue" [href]="issue.url" target="_blank" rel="noopener">issue #{{ issue.number }}</a>
            }
            @if (run.integration_branch) { <code>{{ run.integration_branch }}</code> }
            @if (run.pr_url) {
              <a [href]="run.pr_url" target="_blank" rel="noopener" class="pr">Pull request ↗</a>
            }
            <span class="stream" [class]="'stream ' + streamState()">events: {{ streamState() }}</span>
          </div>
        </div>
        @if (!terminal()) {
          <button mat-stroked-button color="warn" (click)="cancel()" [disabled]="submitting()">Cancel run</button>
        }
      </header>
      @if (run.busy || submitting()) {
        <mat-progress-bar mode="indeterminate" />
      }
      @if (run.error) {
        <p class="error">{{ run.error }}</p>
      }

      @if (attention().length) {
        <div class="attention" role="status">
          <span class="pulse"></span> Waiting for you:
          @for (n of attention(); track n.id) {
            <button type="button" class="chip" (click)="pick(n.id)">{{ n.label }}</button>
          }
        </div>
      }

      @if (workflow(); as wf) {
        <section class="board" [class.with-panel]="selectedWorkflowNode()">
          <app-workflow-graph class="graph" [workflow]="wf" [selected]="selectedNode()"
                              (nodeSelected)="pick($event)" />
          @if (selectedWorkflowNode(); as node) {
            <aside class="panel dc-glass" aria-label="Selected step">
              <app-workflow-panel [node]="node" [run]="run" [busy]="submitting()"
                                  (resumeRequested)="resume($event)" (closed)="pick(null)" />
            </aside>
          }
        </section>
      }

      <mat-tab-group animationDuration="0ms" [selectedIndex]="tab()" (selectedIndexChange)="tab.set($event)">
        <mat-tab label="Timeline">
          <app-event-timeline [events]="events()" />
        </mat-tab>
        <mat-tab label="Plan"><app-plan-view [plan]="run.plan" /></mat-tab>
        <mat-tab label="Design"><app-design-view [design]="run.design" /></mat-tab>
        <mat-tab [label]="'Q&A (' + run.qa_log.length + ')'"><app-qa-panel [entries]="run.qa_log" /></mat-tab>
        <mat-tab label="Files">
          @defer (on viewport) {
            <app-file-explorer [runId]="run.id" [tasks]="run.tasks" />
          } @placeholder {
            <p>Loading…</p>
          }
        </mat-tab>
      </mat-tab-group>
    } @else if (!error()) {
      <mat-progress-bar mode="indeterminate" />
    }
  `,
  styles: `
    .back { text-decoration: none; }
    .head { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; }
    h1 { margin: 8px 0 4px; font-size: 22px; }
    .meta { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; color: var(--dc-text-dim); font-size: 13px; }
    .stream { font-size: 12px; color: var(--dc-text-faint); } .stream.open { color: var(--dc-teal); } .stream.reconnecting { color: var(--dc-amber); }
    .pr { font-weight: 600; }
    .badge { font-size: 12px; padding: 1px 10px; border-radius: 12px; color: var(--dc-violet);
             border: 1px solid rgba(163, 132, 255, 0.5); background: rgba(163, 132, 255, 0.1); }
    .error { color: var(--dc-red); }
    .attention { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin: 12px 0 0; padding: 8px 12px;
                 border-radius: 10px; border: 1px solid rgba(255, 193, 77, 0.5); background: rgba(255, 193, 77, 0.08);
                 color: var(--dc-amber); font-size: 13px; font-weight: 600; }
    .pulse { width: 9px; height: 9px; border-radius: 50%; background: var(--dc-amber); animation: dc-pulse-amber 1.4s infinite; }
    .chip { font: inherit; font-weight: 600; cursor: pointer; color: #231600; background: var(--dc-amber);
            border: 0; border-radius: 12px; padding: 3px 12px; box-shadow: var(--dc-glow-amber); }
    .board { display: grid; grid-template-columns: 1fr; gap: 12px; margin-top: 12px; height: min(70vh, 660px); }
    .board.with-panel { grid-template-columns: minmax(0, 1fr) 440px; }
    .graph { height: 100%; min-width: 0; }
    .panel { overflow: auto; min-height: 0; }
    @media (max-width: 1000px) {
      .board, .board.with-panel { grid-template-columns: 1fr; height: auto; }
      .graph { height: 60vh; }
    }
    mat-tab-group { margin-top: 16px; }
  `,
})
export class RunDetailComponent {
  private readonly api = inject(ApiService);
  private readonly streams = inject(RunEventsService);
  private readonly destroyRef = inject(DestroyRef);

  /** Route parameter (component input binding). */
  readonly id = input.required<string>();

  readonly run = signal<RunDetail | null>(null);
  readonly error = signal<string | null>(null);
  readonly submitting = signal(false);
  readonly workflow = signal<Workflow | null>(null);
  readonly selectedNode = signal<string | null>(null);
  readonly tab = signal(0);
  private readonly stream = signal<RunEventStream | null>(null);
  private refreshTimer: ReturnType<typeof setTimeout> | null = null;
  private seenAttention = new Set<string>();
  /** The panel was opened by us (not by the user): it may move on as the work moves on. */
  private autoSelected = false;

  readonly events = computed<RunEvent[]>(() => this.stream()?.events() ?? []);
  readonly streamState = computed(() => this.stream()?.state() ?? 'closed');
  readonly terminal = computed(() => {
    const status = this.run()?.status;
    return status !== undefined && TERMINAL_STATUSES.includes(status);
  });
  readonly title = computed(() => requestTitle(this.run()?.request ?? ''));
  readonly nodesById = computed(
    () => new Map((this.workflow()?.nodes ?? []).map((n) => [n.id, n] as const)),
  );
  readonly selectedWorkflowNode = computed<WorkflowNode | null>(() => {
    const id = this.selectedNode();
    return id ? (this.nodesById().get(id) ?? null) : null;
  });
  readonly attention = computed(() =>
    (this.workflow()?.attention ?? [])
      .map((id) => this.nodesById().get(id))
      .filter((n): n is WorkflowNode => n !== undefined),
  );

  constructor() {
    effect(() => {
      const id = this.id();
      untracked(() => this.open(id));
    });
    this.destroyRef.onDestroy(() => {
      this.stream()?.close();
      if (this.refreshTimer) {
        clearTimeout(this.refreshTimer);
      }
    });
  }

  resume(request: ResumeRequest): void {
    this.submitting.set(true);
    this.api.resume(this.id(), request).subscribe({
      next: () => {
        this.submitting.set(false);
        this.error.set(null);
        this.load();
      },
      error: (err: { error?: { detail?: unknown } }) => {
        this.submitting.set(false);
        this.error.set(`Could not send: ${JSON.stringify(err.error?.detail ?? 'request failed')}`);
        this.load();
      },
    });
  }

  cancel(): void {
    if (!confirm('Cancel this run? Its containers, worktrees and index are removed.')) {
      return;
    }
    this.submitting.set(true);
    this.api.cancel(this.id()).subscribe({
      next: () => {
        this.submitting.set(false);
        this.load();
      },
      error: () => {
        this.submitting.set(false);
        this.load();
      },
    });
  }

  private open(id: string): void {
    this.stream()?.close();
    this.run.set(null);
    this.workflow.set(null);
    this.selectedNode.set(null);
    this.seenAttention = new Set();
    this.stream.set(this.streams.connect(id, () => this.onEvent()));
    this.load();
  }

  private onEvent(): void {
    if (this.refreshTimer) {
      return; // a refresh is already scheduled; it will include this event
    }
    this.refreshTimer = setTimeout(() => {
      this.refreshTimer = null;
      this.load();
    }, REFRESH_DEBOUNCE_MS);
  }

  /** A node chosen by the user (or the panel closed): stays until they change it. */
  pick(id: string | null): void {
    this.autoSelected = false;
    this.selectedNode.set(id);
  }

  /**
   * Open the panel on anything that newly needs the human. A panel we opened ourselves follows
   * the work: once its node is finished it moves to what needs you, or to what is running.
   */
  private focus(wf: Workflow): void {
    const fresh = wf.attention.filter((id) => !this.seenAttention.has(id));
    this.seenAttention = new Set(wf.attention);
    const active = (id: string | null | undefined) => {
      const status = wf.nodes.find((n) => n.id === id)?.status;
      return status === 'running' || status === 'waiting';
    };
    const next = () => wf.attention[0] ?? wf.nodes.find((n) => n.status === 'running')?.id ?? null;
    if (fresh.length) {
      this.selectedNode.set(fresh[0]);
      this.autoSelected = true;
    } else if (this.selectedNode() === null && this.workflow() === null) {
      this.selectedNode.set(next());
      this.autoSelected = true;
    } else if (this.autoSelected && !active(this.selectedNode())) {
      const target = next();
      if (target) {
        this.selectedNode.set(target);
      }
    }
  }

  private load(): void {
    forkJoin({ run: this.api.getRun(this.id()), workflow: this.api.workflow(this.id()) }).subscribe({
      next: ({ run, workflow }) => {
        this.run.set(run);
        this.focus(workflow);
        this.workflow.set(workflow);
        this.error.set(null);
        if (TERMINAL_STATUSES.includes(run.status) && !run.busy) {
          this.stream()?.close(); // finished: stop EventSource from reconnecting forever
        }
      },
      error: (err: { status?: number }) =>
        this.error.set(err.status === 404 ? 'Run not found.' : `Cannot reach ${this.api.baseUrl}.`),
    });
  }
}
