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

import { ResumeRequest, RunDetail, RunEvent, TERMINAL_STATUSES } from '../../core/api.models';
import { ApiService } from '../../core/api.service';
import { RunEventStream, RunEventsService } from '../../core/run-events.service';
import { StatusChipComponent } from '../../shared/status-chip.component';
import { ActionPanelComponent } from './action-panel.component';
import { DesignViewComponent } from './design-view.component';
import { EventTimelineComponent } from './event-timeline.component';
import { FileExplorerComponent } from './file-explorer.component';
import { PlanViewComponent } from './plan-view.component';
import { QaPanelComponent } from './qa-panel.component';
import { TaskDagComponent } from './task-dag.component';
import { TaskDetailComponent } from './task-detail.component';

/** Events after which the run detail is re-fetched (debounced). */
const REFRESH_ON = new Set(['status', 'awaiting_input', 'merge', 'node_finished', 'question', 'answer', 'error']);
const REFRESH_DEBOUNCE_MS = 250;

@Component({
  selector: 'app-run-detail',
  imports: [
    RouterLink, MatTabsModule, MatButtonModule, MatProgressBarModule, StatusChipComponent,
    ActionPanelComponent, EventTimelineComponent, TaskDagComponent, TaskDetailComponent,
    PlanViewComponent, DesignViewComponent, QaPanelComponent, FileExplorerComponent,
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
          <h1>{{ run.request.split('\\n')[0] }}</h1>
          <div class="meta">
            <app-status-chip [status]="run.status" />
            <span>{{ run.repo_target }}</span>
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

      @for (p of run.pending; track p.interrupt_id) {
        <app-action-panel [pending]="p" [busy]="submitting()" [tasks]="run.tasks" (resumeRequested)="resume($event)" />
      }

      <mat-tab-group animationDuration="0ms" [selectedIndex]="tab()" (selectedIndexChange)="tab.set($event)">
        <mat-tab label="Timeline">
          <app-event-timeline [events]="events()" />
        </mat-tab>
        <mat-tab [label]="'Tasks (' + taskCount() + ')'">
          <app-task-dag [plan]="run.plan" [tasks]="run.tasks" [selected]="selectedTask()" (taskSelected)="selectedTask.set($event)" />
          <app-task-detail [runId]="run.id" [task]="selectedPlanTask()" [state]="selectedState()" />
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
    .meta { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; color: #555; font-size: 13px; }
    .stream { font-size: 12px; color: #888; } .stream.open { color: #1b7f3b; } .stream.reconnecting { color: #e8a300; }
    .pr { font-weight: 600; }
    .error { color: #b3261e; }
    mat-tab-group { margin-top: 12px; }
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
  readonly selectedTask = signal<string | null>(null);
  readonly tab = signal(0);
  private readonly stream = signal<RunEventStream | null>(null);
  private refreshTimer: ReturnType<typeof setTimeout> | null = null;

  readonly events = computed<RunEvent[]>(() => this.stream()?.events() ?? []);
  readonly streamState = computed(() => this.stream()?.state() ?? 'closed');
  readonly terminal = computed(() => {
    const status = this.run()?.status;
    return status !== undefined && TERMINAL_STATUSES.includes(status);
  });
  readonly taskCount = computed(
    () => this.run()?.plan?.tasks.length ?? Object.keys(this.run()?.tasks ?? {}).length,
  );
  readonly selectedPlanTask = computed(() => {
    const id = this.selectedTask();
    return this.run()?.plan?.tasks.find((t) => t.id === id) ?? null;
  });
  readonly selectedState = computed(() => {
    const id = this.selectedTask();
    return id ? (this.run()?.tasks[id] ?? null) : null;
  });

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
    this.selectedTask.set(null);
    this.stream.set(this.streams.connect(id, (event) => this.onEvent(event)));
    this.load();
  }

  private onEvent(event: RunEvent): void {
    if (!REFRESH_ON.has(event.type)) {
      return;
    }
    if (this.refreshTimer) {
      clearTimeout(this.refreshTimer);
    }
    this.refreshTimer = setTimeout(() => this.load(), REFRESH_DEBOUNCE_MS);
  }

  private load(): void {
    this.api.getRun(this.id()).subscribe({
      next: (run) => {
        this.run.set(run);
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
