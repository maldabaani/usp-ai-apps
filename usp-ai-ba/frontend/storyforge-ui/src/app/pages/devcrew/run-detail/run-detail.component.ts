import { CommonModule } from '@angular/common';
import { Component, OnDestroy, OnInit } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, RouterLink } from '@angular/router';

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
import { WorkflowCanvasComponent } from './workflow-canvas/workflow-canvas.component';

const POLL_MS = 3000;
const TERMINAL_STATUSES = new Set(['completed', 'failed', 'cancelled']);

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

  private pollHandle: ReturnType<typeof setInterval> | null = null;

  constructor(
    private route: ActivatedRoute,
    private devCrewService: DevCrewService
  ) {}

  ngOnInit(): void {
    this.runId = this.route.snapshot.paramMap.get('runId') ?? '';
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
