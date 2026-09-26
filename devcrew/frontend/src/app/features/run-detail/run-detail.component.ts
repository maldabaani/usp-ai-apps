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
import { DecimalPipe } from '@angular/common';
import { Router, RouterLink } from '@angular/router';
import { catchError, forkJoin, of } from 'rxjs';

import {
  MessageIn,
  ResumeRequest,
  RunDetail,
  RunEvent,
  RunMessage,
  RunUsage,
  TERMINAL_STATUSES,
  Workflow,
  WorkflowNode,
} from '../../core/api.models';
import { ApiService } from '../../core/api.service';
import { NotifyService } from '../../core/notify.service';
import { requestTitle } from '../../core/requirements';
import { formatDuration } from '../../core/workflow-layout';
import { RunEventStream, RunEventsService } from '../../core/run-events.service';
import { StatusChipComponent } from '../../shared/status-chip.component';
import { ActionPanelComponent } from './action-panel.component';
import { ChatComponent } from './chat.component';
import { UsageViewComponent } from './usage-view.component';
import { PreviewComponent } from './preview.component';
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
    FileExplorerComponent, WorkflowGraphComponent, WorkflowPanelComponent, ChatComponent,
    ActionPanelComponent, UsageViewComponent, DecimalPipe, PreviewComponent,
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
            @if (usage(); as u) {
              @if (u.total_tokens) {
                <span class="tokens" title="Tokens and working time (see the Usage tab)">{{ u.total_tokens | number }} tokens
                  · {{ workingTime() }} working</span>
              }
            }
            @if (modelsText(); as m) { <span class="badge models" [title]="'Models chosen for this run: ' + m">models: {{ m }}</span> }
            @if (run.code_search?.status === 'unavailable') {
              <span class="badge warn" [title]="run.code_search?.detail ?? ''">⚠ code search unavailable</span>
            }
            <span class="stream" [class]="'stream ' + streamState()">events: {{ streamState() }}</span>
          </div>
        </div>
        <div class="controls">
          @if (notify.permission() === 'default') {
            <button mat-button class="notify" (click)="notify.enable()" title="Get a browser notification when this run needs you (while the tab is in the background)">🔔 Notify me</button>
          }
          <a mat-stroked-button [href]="reportUrl()" download title="Download the whole run as Markdown">Report ↓</a>
          @if (terminal()) {
            @if (run.status === 'failed') {
              <button mat-flat-button class="resume" (click)="retry()" [disabled]="submitting()"
                      title="Continue from the last checkpoint: the failed step runs again">Retry</button>
            }
            <button mat-stroked-button (click)="runAgain()" title="New run with the same request (you can edit it)">Run again</button>
          } @else {
            @if (run.status === 'paused') {
              <button mat-flat-button class="resume" (click)="setPaused(false)" [disabled]="submitting()">Resume</button>
            } @else if (run.pause_requested) {
              <button mat-stroked-button (click)="setPaused(false)" [disabled]="submitting()">Don't pause</button>
            } @else if (pausable()) {
              <button mat-stroked-button (click)="setPaused(true)" [disabled]="submitting()"
                      title="Stop before the next wave of tasks (running tasks finish first)">Pause</button>
            }
            <button mat-stroked-button color="warn" (click)="cancel()" [disabled]="submitting()">Cancel run</button>
          }
        </div>
      </header>
      @if (run.busy || submitting()) {
        <mat-progress-bar mode="indeterminate" />
      }
      @if (run.error) {
        <p class="error">{{ run.error }}</p>
      }

      @if (run.status === 'paused') {
        <div class="paused" role="status">⏸ Paused before the next wave. Messages you send now are
          applied when you resume.</div>
      } @else if (run.pause_requested) {
        <div class="paused pending" role="status">Pausing after the current tasks finish…</div>
      }

      @for (p of runLevelPending(); track p.interrupt_id) {
        <div class="run-level">
          <app-action-panel [pending]="p" [busy]="submitting()" (resumeRequested)="resume($event)" />
        </div>
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
              <app-workflow-panel [node]="node" [run]="run" [busy]="submitting()" [messages]="messages()"
                                  (resumeRequested)="resume($event)" (messageSent)="sendMessage($event)"
                                  (closed)="pick(null)" />
            </aside>
          }
        </section>
      }

      <mat-tab-group animationDuration="0ms" [selectedIndex]="tab()" (selectedIndexChange)="tab.set($event)">
        <mat-tab label="Timeline">
          <app-event-timeline [events]="events()" />
        </mat-tab>
        <mat-tab [label]="'Chat (' + messages().length + ')'">
          <div class="chat-tab">
            @if (run.human_notes?.length) {
              <div class="notes">
                <b>The crew follows these notes from you</b>
                <ul>@for (n of run.human_notes!; track $index) {
                  <li>{{ n.text }}@if (n.merged) { <span class="merged">merged</span> }</li>
                }</ul>
              </div>
            }
            <app-chat [messages]="messages()" [tasks]="taskOptions()" [disabled]="terminal()" [editable]="true"
                      [busy]="submitting()" (send)="sendMessage($event)" (edited)="editMessage($event)"
                      (withdraw)="withdrawMessage($event)" />
          </div>
        </mat-tab>
        <mat-tab label="Usage"><app-usage-view [usage]="usage()" /></mat-tab>
        <mat-tab label="Plan"><app-plan-view [plan]="run.plan" /></mat-tab>
        <mat-tab label="Design"><app-design-view [design]="run.design" /></mat-tab>
        <mat-tab [label]="'Q&A (' + run.qa_log.length + ')'"><app-qa-panel [entries]="run.qa_log" /></mat-tab>
        <mat-tab label="Preview">
          @defer (on viewport) {
            <app-preview [runId]="run.id" />
          } @placeholder {
            <p>Loading…</p>
          }
        </mat-tab>
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
    .controls { display: flex; gap: 8px; }
    .resume { box-shadow: var(--dc-glow-amber); }
    .paused { margin: 12px 0 0; padding: 8px 12px; border-radius: 10px; font-size: 13px; font-weight: 600;
              color: var(--dc-amber); border: 1px solid rgba(255, 193, 77, 0.5); background: rgba(255, 193, 77, 0.08); }
    .paused.pending { color: var(--dc-text-dim); border-color: var(--dc-border-strong); background: transparent; }
    .chat-tab { padding: 12px 4px; max-width: 980px; }
    .notes { font-size: 13px; margin: 0 0 10px; padding: 8px 12px; border-radius: 8px;
             border: 1px solid rgba(45, 226, 176, 0.35); background: rgba(45, 226, 176, 0.06); }
    .notes b { color: var(--dc-teal); }
    .notes ul { margin: 4px 0 0; padding-left: 18px; }
    .merged { margin-left: 6px; font-size: 11px; color: var(--dc-text-faint); }
    .tokens { font-size: 12px; color: var(--dc-text-dim); font-family: var(--dc-mono); }
    .run-level { margin-top: 12px; }
    .badge.warn { color: var(--dc-amber); border-color: rgba(255, 193, 77, 0.55); background: rgba(255, 193, 77, 0.1); }
    .badge.models { color: var(--dc-cyan); border-color: rgba(34, 211, 238, 0.45); background: rgba(34, 211, 238, 0.08);
                    max-width: 420px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .controls { flex-wrap: wrap; justify-content: flex-end; }
  `,
})
export class RunDetailComponent {
  private readonly api = inject(ApiService);
  private readonly streams = inject(RunEventsService);
  private readonly destroyRef = inject(DestroyRef);
  readonly notify = inject(NotifyService);

  /** Route parameter (component input binding). */
  readonly id = input.required<string>();

  readonly run = signal<RunDetail | null>(null);
  readonly error = signal<string | null>(null);
  readonly submitting = signal(false);
  readonly workflow = signal<Workflow | null>(null);
  readonly messages = signal<RunMessage[]>([]);
  readonly usage = signal<RunUsage | null>(null);
  private readonly router = inject(Router);
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
  /** A pause takes effect before the next wave of tasks, so only until development ends. */
  readonly pausable = computed(() =>
    ['pending', 'preparing', 'planning', 'awaiting_plan_approval', 'designing', 'awaiting_design_approval',
     'scaffolding', 'executing', 'needs_human'].includes(this.run()?.status ?? ''),
  );
  /** Decisions about the whole run that belong to no workflow node (the budget). */
  readonly runLevelPending = computed(() => (this.run()?.pending ?? []).filter((p) => p.kind === 'budget'));
  readonly workingTime = computed(() => formatDuration((this.usage()?.active_s ?? 0) * 1000));
  readonly taskOptions = computed(() =>
    (this.run()?.plan?.tasks ?? []).map((t) => ({ id: t.id, title: t.title })),
  );
  readonly title = computed(() => requestTitle(this.run()?.request ?? ''));
  readonly reportUrl = computed(() => this.api.reportUrl(this.id()));
  readonly modelsText = computed(() =>
    Object.entries(this.run()?.models ?? {})
      .map(([role, model]) => `${role} ${model}`)
      .join(' · '),
  );
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

  sendMessage(body: MessageIn): void {
    this.submitting.set(true);
    this.api.sendMessage(this.id(), body).subscribe({
      next: (m) => {
        this.submitting.set(false);
        this.messages.update((ms) => [...ms, m]);
      },
      error: (err: { error?: { detail?: unknown } }) => {
        this.submitting.set(false);
        this.error.set(`Could not send the message: ${JSON.stringify(err.error?.detail ?? 'request failed')}`);
      },
    });
  }

  editMessage(change: { id: number; text: string }): void {
    this.api.editMessage(this.id(), change.id, change.text).subscribe({
      next: (m) => this.messages.update((ms) => ms.map((x) => (x.id === m.id ? m : x))),
      error: (err: { error?: { detail?: unknown } }) =>
        this.error.set(`Could not edit the message: ${JSON.stringify(err.error?.detail ?? 'request failed')}`),
    });
  }

  withdrawMessage(id: number): void {
    this.api.withdrawMessage(this.id(), id).subscribe({
      next: (m) => this.messages.update((ms) => ms.map((x) => (x.id === m.id ? m : x))),
      error: (err: { error?: { detail?: unknown } }) =>
        this.error.set(`Could not withdraw the message: ${JSON.stringify(err.error?.detail ?? 'request failed')}`),
    });
  }

  retry(): void {
    this.submitting.set(true);
    this.api.retry(this.id()).subscribe({
      next: () => {
        this.submitting.set(false);
        this.stream()?.close();
        this.stream.set(this.streams.connect(this.id(), () => this.onEvent()));
        this.load();
      },
      error: (err: { error?: { detail?: unknown } }) => {
        this.submitting.set(false);
        this.error.set(`Could not retry: ${JSON.stringify(err.error?.detail ?? 'request failed')}`);
      },
    });
  }

  runAgain(): void {
    void this.router.navigate(['/runs/new'], { queryParams: { from: this.id() } });
  }

  setPaused(paused: boolean): void {
    this.submitting.set(true);
    this.api.pause(this.id(), paused).subscribe({
      next: () => {
        this.submitting.set(false);
        this.load();
      },
      error: (err: { error?: { detail?: unknown } }) => {
        this.submitting.set(false);
        this.error.set(`Could not ${paused ? 'pause' : 'resume'}: ${JSON.stringify(err.error?.detail ?? 'request failed')}`);
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
    this.messages.set([]);
    this.usage.set(null);
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
    if (fresh.length && this.workflow() !== null) {
      const labels = fresh.map((id) => wf.nodes.find((n) => n.id === id)?.label ?? id);
      this.notify.notify(`DevCrew needs you: ${this.title()}`, labels.join(', '), `devcrew-${this.id()}`, () =>
        this.pick(fresh[0]),
      );
    }
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
    forkJoin({
      run: this.api.getRun(this.id()),
      workflow: this.api.workflow(this.id()),
      messages: this.api.messages(this.id()).pipe(catchError(() => of(null))),
      usage: this.api.usage(this.id()).pipe(catchError(() => of(null))),
    }).subscribe({
      next: ({ run, workflow, messages, usage }) => {
        const before = this.run()?.status;
        if (before && before !== run.status && ['completed', 'failed'].includes(run.status)) {
          this.notify.notify(`DevCrew run ${run.status}: ${requestTitle(run.request)}`,
            run.pr_url ?? run.error ?? '', `devcrew-${run.id}`);
        }
        this.run.set(run);
        if (usage) {
          this.usage.set(usage);
        }
        if (messages) {
          this.messages.set(messages);
        }
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
