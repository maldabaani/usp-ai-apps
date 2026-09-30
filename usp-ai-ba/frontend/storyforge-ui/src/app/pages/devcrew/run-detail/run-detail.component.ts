import { CommonModule } from '@angular/common';
import { Component, OnDestroy, OnInit } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, RouterLink } from '@angular/router';

import { environment } from '../../../../environments/environment';
import { AuthService } from '../../../services/auth.service';
import {
  DevCrewService,
  MessageOut,
  PendingInput,
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
    private authService: AuthService
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
    return this.run?.pending?.[0] ?? null;
  }

  get selectedNode(): WorkflowNode | null {
    return this.workflow?.nodes.find((n) => n.id === this.selectedNodeId) ?? null;
  }

  selectNode(id: string): void {
    this.selectedNodeId = id;
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
    return !!node && !!this.workflow?.attention.includes(node.id) && !!this.pendingGate;
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
  }

  resolveGate(action: 'approve' | 'reject'): void {
    const gate = this.pendingGate;
    if (!gate) {
      return;
    }
    this.gateBusy = true;
    this.gateError = '';
    this.devCrewService
      .resumeRun(this.runId, action, {
        feedback: this.gateFeedback || null,
        interrupt_id: gate.interrupt_id,
      })
      .subscribe({
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

  statusClass(status: string): string {
    if (status.startsWith('awaiting_') || status === 'needs_human' || status === 'paused') {
      return 'dc-status-waiting';
    }
    if (status === 'completed' || status === 'done') return 'dc-status-done';
    if (status === 'failed' || status === 'cancelled') return 'dc-status-error';
    return 'dc-status-active';
  }
}
