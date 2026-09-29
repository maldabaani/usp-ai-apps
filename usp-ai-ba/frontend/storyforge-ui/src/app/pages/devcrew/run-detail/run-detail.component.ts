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
  Workflow,
} from '../../../services/devcrew.service';

const POLL_MS = 3000;

@Component({
  selector: 'app-devcrew-run-detail',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink],
  templateUrl: './run-detail.component.html',
  styleUrl: './run-detail.component.css',
})
export class RunDetailComponent implements OnInit, OnDestroy {
  runId = '';
  run: RunDetail | null = null;
  workflow: Workflow | null = null;
  messages: MessageOut[] = [];
  usage: RunUsage | null = null;

  loading = true;
  loadError = '';

  newMessage = '';
  sendingMessage = false;

  gateBusy = false;
  gateFeedback = '';
  gateError = '';

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
      next: (workflow) => (this.workflow = workflow),
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

  get taskList(): { id: string; title: string; stack: string; status: string }[] {
    if (!this.run) {
      return [];
    }
    const planTasks = this.run.plan?.tasks ?? [];
    if (planTasks.length) {
      return planTasks.map((t) => ({
        id: t.id,
        title: t.title,
        stack: t.stack,
        status: this.run!.tasks[t.id]?.status ?? 'pending',
      }));
    }
    return Object.values(this.run.tasks).map((t) => ({
      id: t.id,
      title: t.id,
      stack: '',
      status: t.status,
    }));
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

  nodeStatusClass(status: string): string {
    if (status === 'done') return 'dc-node-done';
    if (status === 'failed') return 'dc-node-failed';
    if (status === 'running') return 'dc-node-running';
    if (status === 'waiting') return 'dc-node-waiting';
    if (status === 'skipped') return 'dc-node-skipped';
    return 'dc-node-pending';
  }
}
