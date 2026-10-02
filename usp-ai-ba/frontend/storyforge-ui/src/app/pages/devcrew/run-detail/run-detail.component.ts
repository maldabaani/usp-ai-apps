import { CommonModule } from '@angular/common';
import { ChangeDetectorRef, Component, OnDestroy, OnInit } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, RouterLink } from '@angular/router';

import { environment } from '../../../../environments/environment';
import { AuthService } from '../../../services/auth.service';
import {
  DevCrewService,
  MessageOut,
  PendingInput,
  PromptEntry,
  PromptList,
  RunDetail,
  RunUsage,
  TaskStateOut,
  Workflow,
  WorkflowNode,
} from '../../../services/devcrew.service';
import { humanizeStatus } from '../../../services/format-status.util';
import { extractErrorMessage } from '../../../services/http-error.util';
import { CanvasViewMode, WorkflowCanvasComponent } from './workflow-canvas/workflow-canvas.component';

const POLL_MS = 3000;
const TERMINAL_STATUSES = new Set(['completed', 'failed', 'cancelled']);

// Shapes of PendingInput.data this component renders structured (not raw JSON) views for.
// All three come straight off the matching Python dataclasses/Pydantic models in
// devcrew/graph/nodes/approvals.py (final approval), the Coordinator escalation payload, and
// devcrew/graph/nodes/finish.py's delivery_failed node -- kept loosely typed (optional fields)
// since this is JSON crossing a process boundary, not a compile-time guarantee.
interface GateReviewIssue {
  file: string;
  line: number | null;
  severity: string;
  message: string;
  rule_ref: string | null;
}

interface GateReview {
  decision: string;
  summary: string;
  issues: GateReviewIssue[];
}

interface GateTestResults {
  ran: boolean;
  passed: boolean;
  failed: string[];
  logs_excerpt: string;
  command: string;
}

interface FinalApprovalTask {
  id: string;
  status: string;
  branch: string | null;
  iterations: number;
  review: GateReview | null;
  test_results: GateTestResults | null;
  feedback: string | null;
  error: string | null;
  coordinator_actions: number;
}

interface GateCheckResult {
  name: string;
  status: string;
  summary: string;
  details: string[];
  allowable: boolean;
}

interface FinalApprovalIntegration {
  merged: string[];
  failed: string[];
  blocked: string[];
  cancelled: string[];
  tests: Record<string, GateTestResults>;
  coverage: Record<string, number>;
}

function formatDuration(seconds: number): string {
  const total = Math.round(seconds);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

@Component({
  selector: 'app-devcrew-run-detail',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink, WorkflowCanvasComponent],
  templateUrl: './run-detail.component.html',
  styleUrl: './run-detail.component.css',
})
export class RunDetailComponent implements OnInit, OnDestroy {
  runId = '';
  run: RunDetail | null = null;
  workflow: Workflow | null = null;
  messages: MessageOut[] = [];
  usage: RunUsage | null = null;

  prompts: PromptList | null = null;
  promptsOpen = false;
  promptFilterTaskId: string | null = null;
  promptFilterNode: string | null = null;
  openPromptEntryIds = new Set<number>();
  copiedPromptMessageKey: string | null = null;

  selectedNodeId: string | null = null;
  readonly humanizeStatus = humanizeStatus;

  loading = true;
  loadError = '';

  newMessage = '';
  sendingMessage = false;

  gateBusy = false;
  gateFeedback = '';
  gateError = '';

  retryBusy = false;
  retryError = '';

  pauseBusy = false;
  pauseError = '';

  cancelBusy = false;
  cancelError = '';

  canvasViewMode: CanvasViewMode = 'follow';
  canvasShowMinimap = false;

  notifyEnabled = false;
  private lastNotifiedAttentionKey = '';

  taskMessageDraft = '';
  sendingTaskMessage = false;

  readonly formatDuration = formatDuration;

  private pollHandle: ReturnType<typeof setInterval> | null = null;

  constructor(
    private route: ActivatedRoute,
    private devCrewService: DevCrewService,
    private authService: AuthService,
    private changeDetectorRef: ChangeDetectorRef
  ) {}

  ngOnInit(): void {
    this.runId = this.route.snapshot.paramMap.get('runId') ?? '';
    this.notifyEnabled = typeof localStorage !== 'undefined' && localStorage.getItem(this.notifyStorageKey) === '1';
    this.load();
    this.pollHandle = setInterval(() => this.load(), POLL_MS);
  }

  ngOnDestroy(): void {
    if (this.pollHandle) {
      clearInterval(this.pollHandle);
    }
  }

  load(): void {
    if (!this.runId) {
      return;
    }
    this.devCrewService.getRun(this.runId).subscribe({
      next: (run) => {
        this.run = run;
        this.loading = false;
        this.loadError = '';
      },
      error: () => {
        this.loading = false;
        this.loadError = 'Unable to load this DevCrew run.';
      },
    });
    this.devCrewService.getWorkflow(this.runId).subscribe({
      next: (workflow) => {
        this.workflow = workflow;
        this.autoSelectNode(workflow);
        this.maybeNotifyAttention(workflow);
      },
      error: () => {},
    });
    this.devCrewService.listMessages(this.runId).subscribe({
      next: (messages) => (this.messages = messages),
      error: () => {},
    });
    this.devCrewService.getUsage(this.runId).subscribe({
      next: (usage) => (this.usage = usage),
      error: () => {},
    });
  }

  get pendingGate(): PendingInput | null {
    const pending = this.run?.pending ?? [];
    // Match the interrupt to the *selected* node instead of always taking
    // pending[0] -- more than one gate (e.g. a task escalation and the
    // final approval) can be open at once, and each node should show its
    // own.
    const ids = this.selectedNode?.pending_interrupt_ids ?? [];
    return pending.find((p) => ids.includes(p.interrupt_id)) ?? pending[0] ?? null;
  }

  gateOptionText(gate: PendingInput, action: string): string | null {
    const options = gate.data?.['options'];
    if (!options || typeof options !== 'object') {
      return null;
    }
    const text = (options as Record<string, unknown>)[action];
    return typeof text === 'string' ? text : null;
  }

  // Which structured view (if any) the gate-resolve form below renders for the pending gate's
  // data, instead of the raw-JSON fallback every gate used to get regardless of shape.
  get gateShape(): 'final-approval' | 'task-escalation' | 'delivery-failure' | 'generic' {
    const gate = this.pendingGate;
    if (!gate) return 'generic';
    if (gate.kind === 'approval' && gate.artifact === 'final') return 'final-approval';
    if (gate.kind === 'escalation' && gate.data?.['task_id']) return 'task-escalation';
    if (gate.kind === 'escalation' && gate.data?.['node'] === 'github_delivery') {
      return 'delivery-failure';
    }
    return 'generic';
  }

  get finalApprovalTasks(): FinalApprovalTask[] {
    const tasks = (this.pendingGate?.data?.['tasks'] ?? {}) as Record<string, FinalApprovalTask>;
    return Object.values(tasks);
  }

  get finalApprovalIntegration(): FinalApprovalIntegration | null {
    return (this.pendingGate?.data?.['integration'] as FinalApprovalIntegration | undefined) ?? null;
  }

  get finalApprovalGates(): GateCheckResult[] {
    const gates = this.pendingGate?.data?.['gates'] as { results?: GateCheckResult[] } | undefined;
    return gates?.results ?? [];
  }

  get finalApprovalUnfinished(): string {
    return (this.pendingGate?.data?.['unfinished'] as string) || '';
  }

  get escalationTaskTitle(): string {
    const task = this.pendingGate?.data?.['task'] as { title?: string } | undefined;
    return task?.title ?? '';
  }

  get escalationReason(): string {
    return (this.pendingGate?.data?.['reason'] as string) ?? '';
  }

  get escalationQuestion(): string {
    return (this.pendingGate?.data?.['question'] as string) ?? '';
  }

  get escalationIterations(): number | null {
    const v = this.pendingGate?.data?.['iterations'];
    return typeof v === 'number' ? v : null;
  }

  get escalationReview(): GateReview | null {
    return (this.pendingGate?.data?.['review'] as GateReview | null) ?? null;
  }

  get escalationTestResults(): GateTestResults | null {
    return (this.pendingGate?.data?.['test_results'] as GateTestResults | null) ?? null;
  }

  testBadgeClass(passed: boolean): string {
    return this.statusClass(passed ? 'passed' : 'failed');
  }

  get selectedNode(): WorkflowNode | null {
    return this.workflow?.nodes.find((n) => n.id === this.selectedNodeId) ?? null;
  }

  selectNode(id: string): void {
    this.selectedNodeId = id;
    // The gate form's textarea/error are a single shared property (one
    // #gateForm template reused for whichever node is selected) -- without
    // this, switching nodes would leave a draft typed for one task's gate
    // still sitting in the next task's gate form.
    this.gateFeedback = '';
    this.gateError = '';
  }

  get panelKind(): 'empty' | 'requirements' | 'plan' | 'design' | 'approval' | 'task' | 'generic' {
    const node = this.selectedNode;
    if (!node) return 'empty';
    if (node.id === 'requirements') return 'requirements';
    if (node.id === 'planner' && this.run?.plan) return 'plan';
    if (node.id === 'architect' && this.run?.design) return 'design';
    if (node.kind === 'approval') return 'approval';
    if (node.kind === 'task') return 'task';
    return 'generic';
  }

  get selectedTask(): TaskStateOut | null {
    const node = this.selectedNode;
    if (!node?.task_id || !this.run) {
      return null;
    }
    return this.run.tasks[node.task_id] ?? null;
  }

  get selectedNodeNeedsGate(): boolean {
    const node = this.selectedNode;
    return (
      !!node &&
      !this.runIsTerminal &&
      !!this.workflow?.attention.includes(node.id) &&
      !!this.pendingGate
    );
  }

  get selectedTaskMessages(): MessageOut[] {
    const taskId = this.selectedNode?.task_id;
    return taskId ? this.messages.filter((m) => m.task_id === taskId) : [];
  }

  sendTaskMessage(): void {
    const taskId = this.selectedNode?.task_id;
    const text = this.taskMessageDraft.trim();
    if (!taskId || !text) {
      return;
    }
    this.sendingTaskMessage = true;
    this.devCrewService.postMessage(this.runId, text, taskId).subscribe({
      next: (msg) => {
        this.messages = [...this.messages, msg];
        this.taskMessageDraft = '';
        this.sendingTaskMessage = false;
      },
      error: () => {
        this.sendingTaskMessage = false;
      },
    });
  }

  get reportDownloadUrl(): string {
    // A plain <a href> download isn't routed through HttpClient, so the
    // auth interceptor never sees it -- the token has to ride along as a
    // query param instead.
    const token = this.authService.getToken();
    const url = `${environment.apiBaseUrl}/devcrew/runs/${this.runId}/report.md`;
    return token ? `${url}?token=${encodeURIComponent(token)}` : url;
  }

  private get notifyStorageKey(): string {
    return `dc-notify-${this.runId}`;
  }

  toggleNotify(): void {
    if (this.notifyEnabled) {
      this.notifyEnabled = false;
      localStorage.removeItem(this.notifyStorageKey);
      return;
    }
    if (typeof Notification === 'undefined') {
      return;
    }
    if (Notification.permission === 'granted') {
      this.notifyEnabled = true;
      localStorage.setItem(this.notifyStorageKey, '1');
      return;
    }
    Notification.requestPermission().then((permission) => {
      if (permission === 'granted') {
        this.notifyEnabled = true;
        localStorage.setItem(this.notifyStorageKey, '1');
      }
    });
  }

  private maybeNotifyAttention(workflow: Workflow): void {
    if (!this.notifyEnabled || typeof Notification === 'undefined' || !workflow.attention.length) {
      return;
    }
    const key = workflow.attention.join(',');
    if (key === this.lastNotifiedAttentionKey) {
      return;
    }
    this.lastNotifiedAttentionKey = key;
    new Notification('DevCrew needs your input', {
      body: this.run?.request ?? 'A run is waiting for you.',
      tag: this.runId,
    });
  }

  private autoSelectNode(workflow: Workflow): void {
    const stillPresent = workflow.nodes.some((n) => n.id === this.selectedNodeId);
    if (this.selectedNodeId && stillPresent) {
      return;
    }
    const attention = workflow.attention[0];
    const running = workflow.nodes.find((n) => n.status === 'running')?.id;
    const last = workflow.nodes[workflow.nodes.length - 1]?.id;
    this.selectedNodeId = attention ?? running ?? last ?? null;
    this.gateFeedback = '';
    this.gateError = '';
  }

  resolveGate(action: 'approve' | 'answer' | 'reject'): void {
    const gate = this.pendingGate;
    if (!gate) {
      return;
    }
    this.gateBusy = true;
    this.gateError = '';
    const extra: Record<string, unknown> = { interrupt_id: gate.interrupt_id };
    if (action === 'answer') {
      extra['answer'] = this.gateFeedback;
    } else {
      extra['feedback'] = this.gateFeedback || null;
    }
    this.devCrewService.resumeRun(this.runId, action, extra).subscribe({
      next: () => {
        this.gateBusy = false;
        this.gateFeedback = '';
        this.load();
      },
      error: () => {
        this.gateBusy = false;
        this.gateError = 'Could not resolve this gate. Try again.';
      },
    });
  }

  retry(): void {
    if (!this.run || this.run.status !== 'failed') {
      return;
    }
    this.retryBusy = true;
    this.retryError = '';
    this.devCrewService.retryRun(this.runId).subscribe({
      next: () => {
        this.retryBusy = false;
        this.load();
      },
      error: (err) => {
        this.retryBusy = false;
        this.retryError = extractErrorMessage(err, 'Could not retry this run. Try again.');
      },
    });
  }

  get runIsTerminal(): boolean {
    return !!this.run && TERMINAL_STATUSES.has(this.run.status);
  }

  togglePause(): void {
    if (!this.run || this.runIsTerminal) {
      return;
    }
    this.pauseBusy = true;
    this.pauseError = '';
    this.devCrewService.pauseRun(this.runId, !this.run.pause_requested).subscribe({
      next: () => {
        this.pauseBusy = false;
        this.load();
      },
      error: (err) => {
        this.pauseBusy = false;
        this.pauseError = extractErrorMessage(err, 'Could not change the pause state. Try again.');
      },
    });
  }

  cancel(): void {
    if (!this.run || this.runIsTerminal || this.cancelBusy) {
      return;
    }
    if (!confirm('Cancel this run? This cannot be resumed -- you would need to start a new one.')) {
      return;
    }
    this.cancelBusy = true;
    this.cancelError = '';
    this.devCrewService.cancelRun(this.runId).subscribe({
      next: () => {
        this.cancelBusy = false;
        this.load();
      },
      error: (err) => {
        this.cancelBusy = false;
        this.cancelError = extractErrorMessage(err, 'Could not cancel this run. Try again.');
      },
    });
  }

  sendMessage(): void {
    const text = this.newMessage.trim();
    if (!text) {
      return;
    }
    this.sendingMessage = true;
    this.devCrewService.postMessage(this.runId, text).subscribe({
      next: (msg) => {
        this.messages = [...this.messages, msg];
        this.newMessage = '';
        this.sendingMessage = false;
      },
      error: () => {
        this.sendingMessage = false;
      },
    });
  }

  // Also doubles as the badge color for gate-form vocabulary that isn't a node/task run status:
  // review decisions (approve/changes_requested), gate check results (passed/failed/error),
  // and review issue severities (blocker/major/minor/info).
  statusClass(status: string): string {
    if (status.startsWith('awaiting_') || status === 'needs_human' || status === 'paused') {
      return 'dc-status-waiting';
    }
    if (['completed', 'done', 'approve', 'approved', 'passed', 'merged'].includes(status)) {
      return 'dc-status-done';
    }
    if (
      ['failed', 'cancelled', 'error', 'changes_requested', 'blocked', 'blocker', 'major'].includes(
        status
      )
    ) {
      return 'dc-status-error';
    }
    if (['skipped', 'minor', 'info'].includes(status)) {
      return 'dc-status-waiting';
    }
    return 'dc-status-active';
  }

  togglePrompts(): void {
    this.promptsOpen = !this.promptsOpen;
    if (this.promptsOpen && !this.prompts) {
      this.loadPrompts();
    }
  }

  loadPrompts(): void {
    this.devCrewService.getPrompts(this.runId).subscribe({
      next: (prompts) => (this.prompts = prompts),
      error: () => {},
    });
  }

  togglePromptEntry(id: number): void {
    if (this.openPromptEntryIds.has(id)) {
      this.openPromptEntryIds.delete(id);
    } else {
      this.openPromptEntryIds.add(id);
    }
  }

  isPromptEntryOpen(id: number): boolean {
    return this.openPromptEntryIds.has(id);
  }

  copyPromptMessage(key: string, content: unknown): void {
    navigator.clipboard.writeText(content == null ? '' : String(content)).then(() => {
      this.copiedPromptMessageKey = key;
      this.changeDetectorRef.detectChanges();
      setTimeout(() => {
        if (this.copiedPromptMessageKey === key) {
          this.copiedPromptMessageKey = null;
          this.changeDetectorRef.detectChanges();
        }
      }, 2000);
    });
  }

  get promptTaskIds(): string[] {
    const ids = (this.prompts?.entries ?? [])
      .map((e) => e.task_id)
      .filter((t): t is string => !!t);
    return Array.from(new Set(ids));
  }

  get promptNodes(): string[] {
    const nodes = (this.prompts?.entries ?? [])
      .map((e) => e.node)
      .filter((n): n is string => !!n);
    return Array.from(new Set(nodes));
  }

  get promptGroups(): { label: string; entries: PromptEntry[] }[] {
    const entries = (this.prompts?.entries ?? []).filter(
      (e) =>
        (!this.promptFilterTaskId || e.task_id === this.promptFilterTaskId) &&
        (!this.promptFilterNode || e.node === this.promptFilterNode)
    );
    const byKey = new Map<string, { label: string; entries: PromptEntry[] }>();
    for (const e of entries) {
      const nodeLabel = e.node ?? '(run-level)';
      const label = e.iteration ? `${nodeLabel} · attempt ${e.iteration}` : nodeLabel;
      const key = `${e.node ?? ''}:${e.iteration ?? ''}`;
      if (!byKey.has(key)) {
        byKey.set(key, { label, entries: [] });
      }
      byKey.get(key)!.entries.push(e);
    }
    return Array.from(byKey.values());
  }
}
