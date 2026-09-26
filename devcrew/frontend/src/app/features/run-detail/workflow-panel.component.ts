import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';

import { PendingInput, ResumeRequest, RunDetail, WorkflowNode } from '../../core/api.models';
import { iconKind, nodeElapsed } from '../../core/workflow-layout';
import { AgentIconComponent } from '../../shared/agent-icon.component';
import { MarkdownPipe } from '../../shared/markdown.pipe';
import { ActionPanelComponent } from './action-panel.component';
import { DesignViewComponent } from './design-view.component';
import { PlanViewComponent } from './plan-view.component';
import { TaskDetailComponent } from './task-detail.component';

const ROLE_TEXT: Record<string, string> = {
  requirements: 'Your feature request or requirements document, as the Planner receives it.',
  planner: 'Turns the requirements into user stories and a dependency graph of tasks.',
  approve_plan: 'You approve, reject (with feedback) or edit the plan before any design work.',
  architect: 'Assesses the plan, picks the template and defines modules and contracts.',
  approve_design: 'You approve the design (and read the plan assessment) before development starts.',
  scaffold: 'Creates the repository from the template, installs dependencies and indexes the code.',
  development: 'Developers work on tasks in parallel; each task is reviewed and tested.',
  integration: "Runs the project's full test suite on the integrated branch.",
  approve_final: 'You approve the result; rejecting adds a follow-up task.',
  delivery: 'Pushes the branch and opens the pull request on GitHub.',
};

/** Details and actions for the selected workflow node. */
@Component({
  selector: 'app-workflow-panel',
  imports: [
    DatePipe, MatButtonModule, AgentIconComponent, MarkdownPipe, ActionPanelComponent,
    PlanViewComponent, DesignViewComponent, TaskDetailComponent,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @let n = node();
    <header class="head">
      <app-agent-icon [kind]="icon()" [size]="44" [active]="n.status === 'running'" />
      <div class="titles">
        <h2>{{ n.label }}</h2>
        <div class="meta">
          <span class="state" [class]="'state ' + n.status">{{ n.status }}</span>
          @if (elapsed()) { <span>{{ elapsed() }}</span> }
          @if (n.runs > 1) { <span>{{ n.runs }} runs</span> }
          @if (n.counters['questions']) { <span>{{ n.counters['questions'] }} questions</span> }
        </div>
      </div>
      <button mat-icon-button class="close" (click)="closed.emit()" aria-label="Close panel">✕</button>
    </header>
    @if (roleText()) { <p class="role">{{ roleText() }}</p> }
    @if (n.detail) { <p class="detail">{{ n.detail }}</p> }

    @for (p of pending(); track p.interrupt_id) {
      <app-action-panel [pending]="p" [busy]="busy()" [tasks]="run().tasks" (resumeRequested)="resumeRequested.emit($event)" />
    }

    @switch (view()) {
      @case ('requirements') {
        <article class="markdown doc" [innerHTML]="run().request | markdown"></article>
      }
      @case ('plan') { <app-plan-view [plan]="run().plan" /> }
      @case ('design') { <app-design-view [design]="run().design" /> }
      @case ('task') {
        <app-task-detail [runId]="run().id" [task]="planTask()" [state]="taskState()" />
      }
      @case ('integration') {
        @if (run().integration; as i) {
          <p>Merged: {{ i.merged.join(', ') || '—' }} · failed: {{ i.failed.join(', ') || '—' }}
             · blocked: {{ i.blocked.join(', ') || '—' }}</p>
          @for (entry of testEntries(); track entry[0]) {
            <h4>{{ entry[0] }}: {{ !entry[1].ran ? 'not run' : entry[1].passed ? 'passed' : 'failed' }}</h4>
            @if (entry[1].logs_excerpt) { <pre>{{ entry[1].logs_excerpt }}</pre> }
          }
        }
      }
      @case ('delivery') {
        @if (run().pr_url; as url) {
          <a mat-flat-button [href]="url" target="_blank" rel="noopener">Open pull request ↗</a>
        }
        @if (run().integration_branch; as b) { <p>Branch <code>{{ b }}</code> → {{ run().repo_target }}</p> }
      }
    }

    <h3>Activity</h3>
    @if (n.activity.length) {
      <ol class="activity">
        @for (a of reversed(); track $index) {
          <li [class]="a.kind" [class.bad]="a.ok === false">
            <time>{{ a.at | date: 'HH:mm:ss' }}</time> <span>{{ a.text }}</span>
          </li>
        }
      </ol>
    } @else {
      <p class="muted">{{ n.status === 'pending' ? 'Not started yet.' : 'No tool activity recorded.' }}</p>
    }
  `,
  styles: `
    :host { display: block; padding: 14px 16px; }
    .head { display: flex; gap: 12px; align-items: center; }
    .titles { flex: 1; min-width: 0; }
    h2 { margin: 0; font-size: 17px; }
    .meta { display: flex; gap: 10px; font-size: 12px; color: var(--dc-text-dim); margin-top: 3px; }
    .state { text-transform: uppercase; letter-spacing: 0.6px; font-weight: 700; font-size: 10.5px; }
    .state.running { color: var(--dc-cyan); } .state.waiting { color: var(--dc-amber); }
    .state.done { color: var(--dc-teal); } .state.failed { color: var(--dc-red); }
    .close { color: var(--dc-text-dim); }
    .role { color: var(--dc-text-dim); font-size: 13px; margin: 10px 0 4px; }
    .detail { font-size: 13px; color: var(--dc-cyan); margin: 4px 0; word-break: break-word; }
    .doc { max-height: 420px; overflow: auto; padding: 4px 10px; border-left: 2px solid var(--dc-border-strong); }
    pre { white-space: pre-wrap; background: var(--dc-code-bg); padding: 8px; border-radius: 6px; font-size: 12px; max-height: 260px; overflow: auto; }
    h3 { font-size: 14px; margin: 16px 0 6px; color: var(--dc-cyan); text-transform: uppercase; letter-spacing: 0.8px; }
    .activity { list-style: none; padding: 0; margin: 0; font-size: 12.5px; display: flex; flex-direction: column; gap: 4px; }
    .activity li { padding: 4px 8px; border-radius: 6px; background: rgba(8, 17, 34, 0.7); border-left: 2px solid var(--dc-border-strong); word-break: break-word; }
    .activity li.bad, .activity li.error { border-left-color: var(--dc-red); }
    .activity li.question, .activity li.awaiting_input { border-left-color: var(--dc-amber); }
    .activity li.merge { border-left-color: var(--dc-teal); }
    time { color: var(--dc-text-faint); font-family: var(--dc-mono); margin-right: 4px; }
    .muted { color: var(--dc-text-faint); font-size: 13px; }
  `,
})
export class WorkflowPanelComponent {
  readonly node = input.required<WorkflowNode>();
  readonly run = input.required<RunDetail>();
  readonly busy = input(false);
  readonly now = input(Date.now());
  readonly resumeRequested = output<ResumeRequest>();
  readonly closed = output<void>();

  readonly icon = computed(() => iconKind(this.node()));
  readonly elapsed = computed(() => nodeElapsed(this.node(), this.now()));
  readonly roleText = computed(() =>
    this.node().kind === 'task' ? '' : (ROLE_TEXT[this.node().id] ?? ''),
  );
  readonly reversed = computed(() => [...this.node().activity].reverse());

  readonly view = computed(() => {
    const n = this.node();
    switch (n.id) {
      case 'requirements':
        return 'requirements';
      case 'planner':
      case 'approve_plan':
        return 'plan';
      case 'architect':
      case 'approve_design':
        return 'design';
      case 'integration':
      case 'approve_final':
        return 'integration';
      case 'delivery':
        return 'delivery';
      default:
        return n.kind === 'task' ? 'task' : 'none';
    }
  });

  readonly pending = computed<PendingInput[]>(() => {
    const ids = new Set(this.node().pending_interrupt_ids);
    return this.run().pending.filter((p) => ids.has(p.interrupt_id));
  });
  readonly planTask = computed(
    () => this.run().plan?.tasks.find((t) => t.id === this.node().task_id) ?? null,
  );
  readonly taskState = computed(() => {
    const id = this.node().task_id;
    return id ? (this.run().tasks[id] ?? null) : null;
  });
  readonly testEntries = computed(() => Object.entries(this.run().integration?.tests ?? {}));
}
