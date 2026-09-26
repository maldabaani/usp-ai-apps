import { DatePipe, NgTemplateOutlet } from '@angular/common';
import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';

import {
  MessageIn,
  PendingInput,
  ResumeRequest,
  RunDetail,
  RunMessage,
  TERMINAL_STATUSES,
  WorkflowNode,
} from '../../core/api.models';
import { iconKind, nodeElapsed } from '../../core/workflow-layout';
import { AgentIconComponent } from '../../shared/agent-icon.component';
import { MarkdownPipe } from '../../shared/markdown.pipe';
import { ActionPanelComponent } from './action-panel.component';
import { ChatComponent } from './chat.component';
import { DesignViewComponent } from './design-view.component';
import { PlanViewComponent } from './plan-view.component';
import { TaskDetailComponent } from './task-detail.component';

const ROLE_TEXT: Record<string, string> = {
  requirements: 'Your feature request or requirements document, as the Planner receives it.',
  prepare_repo: 'Clones the repository, detects its projects and test commands, and indexes the code.',
  planner: 'Turns the requirements into user stories and a dependency graph of tasks.',
  approve_plan: 'You approve, reject (with feedback) or edit the plan before any design work.',
  architect: 'Assesses the plan, picks the template and defines modules and contracts.',
  approve_design: 'You approve the design (and read the plan assessment) before development starts.',
  scaffold: 'Creates the repository from the template, installs dependencies and indexes the code.',
  development: 'Developers work on tasks in parallel; each task is reviewed and tested.',
  integration: "Runs the project's full test suite (with coverage) on the integrated branch.",
  gates:
    'Secret scan, dependency vulnerabilities and coverage. Failures get one automatic fix, then you decide.',
  approve_final: 'You approve the result; rejecting adds a follow-up task.',
  delivery: 'Pushes the branch and opens the pull request on GitHub.',
  watch:
    'Polls the pull request: review comments from people with write access (or listed reviewers), ' +
    'failed checks and merge conflicts start a follow-up round. Ends when the PR is merged or closed.',
  followup:
    'The Coordinator sorts the new PR activity: small fixes run automatically, big ones wait for you, ' +
    'questions get an answer on the PR.',
  push: 'Integration tests and gates, then the fixes are pushed to the same PR branch (never forced); ' +
    'each thread gets a reply and fixed threads are resolved.',
};

/** Round nodes are "followup:<n>" / "push:<n>". */
export function roundOf(nodeId: string): number | null {
  const m = /^(?:followup|push):(\d+)$/.exec(nodeId);
  return m ? Number(m[1]) : null;
}

/** Details and actions for the selected workflow node. */
@Component({
  selector: 'app-workflow-panel',
  imports: [
    DatePipe, NgTemplateOutlet, MatButtonModule, AgentIconComponent, MarkdownPipe, ActionPanelComponent,
    PlanViewComponent, DesignViewComponent, TaskDetailComponent, ChatComponent,
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
        @if (!finished() || taskMessages() > 0) {
          <h3>Messages to this task</h3>
          <app-chat [messages]="messages()" [taskId]="n.task_id" [busy]="busy()"
                    [disabled]="finished()" (send)="messageSent.emit($event)" />
        }
        <app-task-detail [runId]="run().id" [task]="planTask()" [state]="taskState()" />
      }
      @case ('repository') {
        @if (run().repo_info; as r) {
          <p>Base branch <code>{{ r.base_branch }}</code> · {{ r.file_count }} files ·
            {{ run().mode === 'quick' ? 'quick fix' : 'full' }} mode ·
            {{ r.source === 'detected' ? 'detected automatically' : 'configured in ' + r.source }}</p>
          <table class="grid">
            <thead><tr><th>Project</th><th>Path</th><th>Tests</th></tr></thead>
            <tbody>
              @for (p of r.projects; track p.path) {
                <tr><td>{{ p.stack }}</td><td><code>{{ p.path }}</code></td><td><code>{{ p.test_cmd }}</code></td></tr>
              }
            </tbody>
          </table>
          @for (note of r.notes; track $index) { <p class="warn">{{ note }}</p> }
          @if (r.readme) {
            <h4>README</h4>
            <article class="markdown doc" [innerHTML]="r.readme | markdown"></article>
          }
        }
      }
      @case ('gates') {
        <ng-container [ngTemplateOutlet]="gatesTable" />
      }
      @case ('integration') {
        @if (node().id === 'approve_final') { <ng-container [ngTemplateOutlet]="gatesTable" /> }
        @if (run().integration; as i) {
          <p>Merged: {{ i.merged.join(', ') || '—' }} · failed: {{ i.failed.join(', ') || '—' }}
             · blocked: {{ i.blocked.join(', ') || '—' }}</p>
          @for (entry of testEntries(); track entry[0]) {
            <h4>{{ entry[0] }}: {{ !entry[1].ran ? 'not run' : entry[1].passed ? 'passed' : 'failed' }}</h4>
            @if (entry[1].logs_excerpt) { <pre>{{ entry[1].logs_excerpt }}</pre> }
          }
        }
      }
      @case ('watch') {
        @if (run().pr_url; as url) {
          <a mat-flat-button [href]="url" target="_blank" rel="noopener">Open pull request ↗</a>
        }
        @if (run().followup?.ignored?.length) {
          <h4>Ignored comments (no write access, not an extra reviewer)</h4>
          <ul class="ignored">@for (i of run().followup?.ignored ?? []; track $index) { <li>{{ i }}</li> }</ul>
        }
      }
      @case ('round') {
        @if (roundTasks().length) {
          <ul class="round-tasks">
            @for (t of roundTasks(); track t.id) {
              <li><b>{{ t.id }}</b> · {{ t.title }} <span class="tstate">{{ run().tasks[t.id] ? run().tasks[t.id].status : 'pending' }}</span></li>
            }
          </ul>
        } @else if (n.status === 'done') {
          <p class="muted">No code changes in this round (replies only).</p>
        }
      }
      @case ('delivery') {
        @if (run().pr_url; as url) {
          <a mat-flat-button [href]="url" target="_blank" rel="noopener">Open pull request ↗</a>
        }
        @if (run().integration_branch; as b) { <p>Branch <code>{{ b }}</code> → {{ run().repo_target }}</p> }
      }
    }

    <ng-template #gatesTable>
      @if (run().gates; as g) {
        @if (g.round) { <p class="muted">After {{ g.round }} automatic fix round(s).</p> }
        <ul class="gates">
          @for (r of g.results; track r.name) {
            <li [class]="r.status">
              <span class="gname">{{ r.name }}</span>
              <span class="gstatus">{{ r.status }}</span>
              <span>{{ r.summary }}</span>
              @if (r.status === 'failed' && !r.allowable) { <span class="never">cannot be allowed</span> }
              @if (r.details.length) {
                <ul>@for (d of r.details; track $index) { <li>{{ d }}</li> }</ul>
              }
            </li>
          }
        </ul>
      } @else {
        <p class="muted">Gates run after the integration tests.</p>
      }
    </ng-template>

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
    .warn { color: var(--dc-amber); font-size: 13px; }
    .grid { border-collapse: collapse; width: 100%; font-size: 12.5px; }
    .grid th, .grid td { text-align: left; padding: 4px 6px; border-bottom: 1px solid var(--dc-border); }
    .gates { list-style: none; padding: 0; display: flex; flex-direction: column; gap: 6px; }
    .gates > li { padding: 8px 10px; border-radius: 8px; background: rgba(8, 17, 34, 0.7); border-left: 3px solid var(--dc-text-faint); font-size: 13px; }
    .gates > li.passed { border-left-color: var(--dc-teal); } .gates > li.failed { border-left-color: var(--dc-red); }
    .gates > li.error { border-left-color: var(--dc-amber); }
    .gates ul { margin: 4px 0 0; padding-left: 18px; font-size: 12px; color: var(--dc-text-dim); word-break: break-word; }
    .gname { font-weight: 700; text-transform: capitalize; margin-right: 8px; }
    .gstatus { text-transform: uppercase; font-size: 10.5px; font-weight: 700; margin-right: 8px; color: var(--dc-text-dim); }
    .passed .gstatus { color: var(--dc-teal); } .failed .gstatus { color: var(--dc-red); } .error .gstatus { color: var(--dc-amber); }
    .round-tasks, .ignored { padding-left: 18px; font-size: 13px; display: flex; flex-direction: column; gap: 4px; }
    .tstate { margin-left: 6px; font-size: 11px; text-transform: uppercase; color: var(--dc-text-dim); }
    .never { margin-left: 8px; font-size: 11px; color: var(--dc-red); border: 1px solid rgba(255, 90, 122, 0.5); border-radius: 8px; padding: 0 6px; }
  `,
})
export class WorkflowPanelComponent {
  readonly node = input.required<WorkflowNode>();
  readonly run = input.required<RunDetail>();
  readonly busy = input(false);
  readonly now = input(Date.now());
  readonly messages = input<RunMessage[]>([]);
  readonly resumeRequested = output<ResumeRequest>();
  readonly messageSent = output<MessageIn>();
  readonly closed = output<void>();

  readonly icon = computed(() => iconKind(this.node()));
  readonly elapsed = computed(() => nodeElapsed(this.node(), this.now()));
  readonly roleText = computed(() =>
    this.node().kind === 'task' ? '' : (ROLE_TEXT[this.node().id] ?? ROLE_TEXT[this.node().id.split(':')[0]] ?? ''),
  );
  /** No more messages once the run or the task has finished. */
  readonly finished = computed(() => {
    const status = this.taskState()?.status;
    return (
      TERMINAL_STATUSES.includes(this.run().status) ||
      (status !== undefined && ['merged', 'failed', 'blocked', 'split', 'cancelled'].includes(status))
    );
  });
  readonly taskMessages = computed(
    () => this.messages().filter((m) => m.task_id === this.node().task_id).length,
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
      case 'prepare_repo':
        return 'repository';
      case 'gates':
        return 'gates';
      case 'delivery':
        return 'delivery';
      case 'watch':
        return 'watch';
      default:
        if (roundOf(n.id) !== null) {
          return 'round';
        }
        return n.kind === 'task' ? 'task' : 'none';
    }
  });

  readonly roundTasks = computed(() => {
    const round = roundOf(this.node().id);
    return round === null
      ? []
      : (this.run().plan?.tasks ?? []).filter((t) => t.id.startsWith(`R${round}-`));
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
