import { CommonModule } from '@angular/common';
import { Component, OnDestroy, OnInit } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';

import {
  IngestFileRecord,
  IngestHistoryEntry,
  IngestResult,
  IngestStatus,
  StoryForgeService,
} from '../../services/storyforge.service';
import { WatchService, WatchTarget } from '../../services/watch.service';

const POLL_INTERVAL_MS = 2000;

type FileStatusFilter = 'all' | 'success' | 'skipped' | 'error' | 'in_progress';

interface DisplayFileRecord extends IngestFileRecord {
  tier: 'mechanical' | 'enrichment';
}

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
  forceFullRechunk = false;
  startingCode = false;
  codeError = '';

  folderPath = '';
  documentsEnableLlmSummary = true;
  documentsMaxConcurrency: number | null = null;
  startingDocuments = false;
  documentsError = '';

  activeJobId = '';
  activeStatus: IngestStatus | null = null;
  cancelling = false;
  cancelError = '';

  history: IngestHistoryEntry[] = [];
  historyLoading = true;
  expandedHistoryJobId: string | null = null;
  clearingHistory = false;
  clearHistoryError = '';

  fileStatusFilter: FileStatusFilter = 'all';
  fileSearch = '';

  watchTargets: WatchTarget[] = [];
  // watchTargetsLoading = true;
  watchPath = '';
  watchKind: 'documents' | 'code' = 'documents';
  addingWatchTarget = false;
  watchError = '';

  private pollHandle: ReturnType<typeof setInterval> | null = null;

  constructor(
    private storyForgeService: StoryForgeService,
    private watchService: WatchService
  ) {}

  ngOnInit(): void {
    this.loadHistory();
    // this.loadWatchTargets();
  }

  ngOnDestroy(): void {
    this.stopPolling();
  }

  // loadWatchTargets(): void {
  //   this.watchTargetsLoading = true;
  //   this.watchService.listTargets().subscribe({
  //     next: (targets) => {
  //       this.watchTargets = targets;
  //       this.watchTargetsLoading = false;
  //     },
  //     error: () => {
  //       this.watchTargetsLoading = false;
  //     },
  //   });
  // }

  // addWatchTarget(): void {
  //   if (!this.watchPath.trim() || this.addingWatchTarget) return;
  //   this.addingWatchTarget = true;
  //   this.watchError = '';

  //   this.watchService.addTarget(this.watchPath.trim(), this.watchKind).subscribe({
  //     next: () => {
  //       this.addingWatchTarget = false;
  //       this.watchPath = '';
  //       this.loadWatchTargets();
  //     },
  //     error: (err) => {
  //       this.addingWatchTarget = false;
  //       this.watchError = err?.error?.detail || 'Failed to add watched path.';
  //     },
  //   });
  // }

  // toggleWatchTarget(target: WatchTarget): void {
  //   this.watchService.setEnabled(target.id, !target.enabled).subscribe({
  //     next: () => this.loadWatchTargets(),
  //   });
  // }

  // removeWatchTarget(target: WatchTarget): void {
  //   if (!confirm(`Stop watching ${target.path}?`)) return;
  //   this.watchService.deleteTarget(target.id).subscribe({
  //     next: () => this.loadWatchTargets(),
  //   });
  // }

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
      .ingestCode(
        this.repoPath.trim(),
        this.enableLlmSummary,
        this.maxConcurrency ?? undefined,
        this.forceFullRechunk
      )
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

    this.storyForgeService
      .ingestDocuments(
        this.folderPath.trim(),
        this.documentsEnableLlmSummary,
        this.documentsMaxConcurrency ?? undefined
      )
      .subscribe({
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

  toggleHistoryDetails(jobId: string): void {
    this.expandedHistoryJobId = this.expandedHistoryJobId === jobId ? null : jobId;
  }

  phaseLabel(status: IngestStatus | null): string {
    if (!status || !status.phase) return '';
    return status.phase === 'enrichment' ? 'Generating LLM summaries' : 'Chunking files';
  }

  clearHistory(): void {
    if (!this.history.length || this.clearingHistory) return;
    if (!confirm('Permanently clear all ingestion history? This cannot be undone.')) return;

    this.clearingHistory = true;
    this.clearHistoryError = '';
    this.storyForgeService.clearIngestHistory().subscribe({
      next: () => {
        this.clearingHistory = false;
        this.history = [];
      },
      error: (err) => {
        this.clearingHistory = false;
        this.clearHistoryError = err?.error?.detail || 'Failed to clear history.';
      },
    });
  }

  private displayFiles(result: IngestResult | null): DisplayFileRecord[] {
    if (!result) return [];
    const mechanical = (result.files || []).map((f) => ({ ...f, tier: 'mechanical' as const }));
    // Tier 2 reports a successful file as 'summarized', not 'success' --
    // normalized here so the shared badge/count/filter logic (which only
    // ever checks for 'success') treats a summarized file as a success too.
    const enrichment = (result.enrichment_files || []).map((f) => ({
      ...f,
      status: f.status === 'summarized' ? ('success' as const) : f.status,
      tier: 'enrichment' as const,
    }));
    return [...mechanical, ...enrichment];
  }

  filteredFiles(result: IngestResult | null): DisplayFileRecord[] {
    const search = this.fileSearch.trim().toLowerCase();
    return this.displayFiles(result).filter(
      (f) =>
        (this.fileStatusFilter === 'all' || f.status === this.fileStatusFilter) &&
        (!search || f.path.toLowerCase().includes(search))
    );
  }

  statusCount(result: IngestResult | null, status: IngestFileRecord['status']): number {
    return this.displayFiles(result).filter((f) => f.status === status).length;
  }

  hasFileBreakdown(result: IngestResult | null): boolean {
    return this.displayFiles(result).length > 0;
  }
}
