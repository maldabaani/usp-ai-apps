import { CommonModule } from '@angular/common';
import { Component, OnDestroy, OnInit } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';

import {
  IngestHistoryEntry,
  IngestStatus,
  StoryForgeService,
} from '../../services/storyforge.service';

const POLL_INTERVAL_MS = 2000;

@Component({
  selector: 'app-ingestion',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink],
  templateUrl: './ingestion.component.html',
  styleUrl: './ingestion.component.css',
})
export class IngestionComponent implements OnInit, OnDestroy {
  repoPath = '';
  enableLlmSummary = true;
  maxConcurrency: number | null = null;
  startingCode = false;
  codeError = '';

  folderPath = '';
  startingDocuments = false;
  documentsError = '';

  activeJobId = '';
  activeStatus: IngestStatus | null = null;
  cancelling = false;
  cancelError = '';

  history: IngestHistoryEntry[] = [];
  historyLoading = true;

  private pollHandle: ReturnType<typeof setInterval> | null = null;

  constructor(private storyForgeService: StoryForgeService) {}

  ngOnInit(): void {
    this.loadHistory();
  }

  ngOnDestroy(): void {
    this.stopPolling();
  }

  loadHistory(): void {
    this.historyLoading = true;
    this.storyForgeService.getIngestHistory().subscribe({
      next: (entries) => {
        this.history = entries;
        this.historyLoading = false;
      },
      error: () => {
        this.historyLoading = false;
      },
    });
  }

  startCodeIngestion(): void {
    if (!this.repoPath.trim() || this.startingCode) return;
    this.startingCode = true;
    this.codeError = '';

    this.storyForgeService
      .ingestCode(this.repoPath.trim(), this.enableLlmSummary, this.maxConcurrency ?? undefined)
      .subscribe({
        next: ({ job_id }) => {
          this.startingCode = false;
          this.trackJob(job_id);
        },
        error: (err) => {
          this.startingCode = false;
          this.codeError = err?.error?.detail || 'Failed to start code ingestion.';
        },
      });
  }

  startDocumentIngestion(): void {
    if (!this.folderPath.trim() || this.startingDocuments) return;
    this.startingDocuments = true;
    this.documentsError = '';

    this.storyForgeService.ingestDocuments(this.folderPath.trim()).subscribe({
      next: ({ job_id }) => {
        this.startingDocuments = false;
        this.trackJob(job_id);
      },
      error: (err) => {
        this.startingDocuments = false;
        this.documentsError = err?.error?.detail || 'Failed to start document ingestion.';
      },
    });
  }

  private trackJob(jobId: string): void {
    this.activeJobId = jobId;
    this.activeStatus = null;
    this.cancelError = '';
    this.poll();
    this.stopPolling();
    this.pollHandle = setInterval(() => this.poll(), POLL_INTERVAL_MS);
  }

  private poll(): void {
    if (!this.activeJobId) return;
    this.storyForgeService.getIngestStatus(this.activeJobId).subscribe({
      next: (status) => {
        this.activeStatus = status;
        if (['done', 'error', 'cancelled'].includes(status.status)) {
          this.stopPolling();
          this.loadHistory();
        }
      },
      error: () => {
        this.stopPolling();
      },
    });
  }

  get canCancel(): boolean {
    return !!this.activeStatus && !['done', 'error', 'cancelled'].includes(this.activeStatus.status);
  }

  cancelActiveJob(): void {
    if (!this.activeJobId || this.cancelling) return;
    if (!confirm('Stop this ingestion job?')) return;

    this.cancelling = true;
    this.cancelError = '';
    this.storyForgeService.cancelIngestJob(this.activeJobId).subscribe({
      next: () => {
        this.cancelling = false;
      },
      error: (err) => {
        this.cancelling = false;
        this.cancelError = err?.error?.detail || 'Cancel failed.';
      },
    });
  }

  private stopPolling(): void {
    if (this.pollHandle) {
      clearInterval(this.pollHandle);
      this.pollHandle = null;
    }
  }

  progressPercent(status: IngestStatus | null): number {
    if (!status || !status.progress.total) return 0;
    return Math.round((status.progress.done / status.progress.total) * 100);
  }

  relativeTime(epochSeconds: number): string {
    const diff = Date.now() - epochSeconds * 1000;
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return 'just now';
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    const days = Math.floor(hrs / 24);
    return `${days}d ago`;
  }
}
