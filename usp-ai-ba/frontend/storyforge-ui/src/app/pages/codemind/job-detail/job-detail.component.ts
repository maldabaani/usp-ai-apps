import { CommonModule } from '@angular/common';
import { Component, OnDestroy, OnInit } from '@angular/core';
import { ActivatedRoute, RouterLink } from '@angular/router';

import {
  CodeMindService,
  ExtractionJob,
  ExtractionResult,
  FailedFile,
  OutputFile,
} from '../../../services/codemind.service';

const POLL_INTERVAL_MS = 2000;
const STEP_ORDER = ['PENDING', 'SCANNING', 'FILTERING', 'PROCESSING', 'COMPLETED'];
const STEP_LABELS: Record<string, string> = {
  PENDING: 'Pending',
  SCANNING: 'Scanning',
  FILTERING: 'Filtering',
  PROCESSING: 'Processing',
  COMPLETED: 'Completed',
};

interface ExtractedRule {
  name?: string;
  description?: string;
  conditions?: string[];
  actions?: string[];
}

interface ExtractedContent {
  summary?: string;
  rules?: ExtractedRule[];
  dependencies?: string[];
}

@Component({
  selector: 'app-codemind-job-detail',
  standalone: true,
  imports: [CommonModule, RouterLink],
  templateUrl: './job-detail.component.html',
  styleUrl: './job-detail.component.css',
})
export class JobDetailComponent implements OnInit, OnDestroy {
  jobId = '';
  job: ExtractionJob | null = null;
  files: OutputFile[] = [];
  failedFiles: FailedFile[] = [];
  loadError = '';

  readonly stepOrder = STEP_ORDER;
  readonly stepLabels = STEP_LABELS;

  cancelling = false;

  viewerOpen = false;
  viewerPath = '';
  viewerLoading = false;
  viewerError = '';
  viewerResult: ExtractionResult | null = null;
  viewerExtracted: ExtractedContent | null = null;
  viewerRaw = '';

  private pollHandle: ReturnType<typeof setInterval> | null = null;
  private failedLoaded = false;

  constructor(
    private route: ActivatedRoute,
    private codeMindService: CodeMindService
  ) {}

  ngOnInit(): void {
    this.jobId = this.route.snapshot.paramMap.get('jobId') ?? '';
    this.refresh();
    this.pollHandle = setInterval(() => this.refresh(), POLL_INTERVAL_MS);
  }

  ngOnDestroy(): void {
    this.stopPolling();
  }

  private stopPolling(): void {
    if (this.pollHandle) {
      clearInterval(this.pollHandle);
      this.pollHandle = null;
    }
  }

  private refresh(): void {
    this.codeMindService.getJob(this.jobId).subscribe({
      next: (job) => {
        this.job = job;
        if (this.isTerminal(job.phase)) {
          this.stopPolling();
          if (job.failedFiles > 0 && !this.failedLoaded) {
            this.failedLoaded = true;
            this.loadFailedFiles();
          }
        }
      },
      error: () => {
        this.loadError = 'Unable to load job.';
        this.stopPolling();
      },
    });
    this.codeMindService.listOutputFiles(this.jobId).subscribe({
      next: (files) => (this.files = files),
      error: () => {
        // Best-effort -- the job poll above already surfaces load failures.
      },
    });
  }

  isTerminal(phase: string): boolean {
    return phase === 'COMPLETED' || phase === 'FAILED' || phase === 'CANCELLED';
  }

  isActive(phase: string): boolean {
    return phase === 'SCANNING' || phase === 'FILTERING' || phase === 'PROCESSING';
  }

  stepState(step: string): 'done' | 'active' | 'pending' {
    if (!this.job) {
      return 'pending';
    }
    const currentIndex = STEP_ORDER.indexOf(this.job.phase);
    if (currentIndex === -1) {
      // FAILED/CANCELLED -- no step highlighted as "current".
      return 'pending';
    }
    const idx = STEP_ORDER.indexOf(step);
    if (idx < currentIndex) {
      return 'done';
    }
    return idx === currentIndex ? 'active' : 'pending';
  }

  cancelJob(): void {
    this.cancelling = true;
    this.codeMindService.cancelJob(this.jobId).subscribe({
      next: () => (this.cancelling = false),
      error: () => (this.cancelling = false),
    });
  }

  getExportUrl(): string {
    return this.codeMindService.getExportUrl(this.jobId);
  }

  private loadFailedFiles(): void {
    this.codeMindService.listFailedFiles(this.jobId).subscribe({
      next: (failed) => (this.failedFiles = failed),
      error: () => {
        // Best-effort -- the progress page still works without this panel.
      },
    });
  }

  stripJsonExt(path: string): string {
    return path.endsWith('.json') ? path.slice(0, -5) : path;
  }

  formatBytes(bytes: number): string {
    return bytes < 1024 ? `${bytes} B` : `${(bytes / 1024).toFixed(1)} KB`;
  }

  truncateError(message: string): string {
    return message.length > 140 ? `${message.slice(0, 140)}…` : message;
  }

  openViewer(relativePath: string): void {
    this.viewerOpen = true;
    this.viewerLoading = true;
    this.viewerError = '';
    this.viewerResult = null;
    this.viewerExtracted = null;
    this.viewerRaw = '';
    this.viewerPath = this.stripJsonExt(relativePath);

    this.codeMindService.readOutputFile(this.jobId, relativePath).subscribe({
      next: (result) => {
        this.viewerLoading = false;
        this.viewerResult = result;
        if (result.success && !result.skipped) {
          try {
            let raw = result.content || '{}';
            // Strip accidental markdown fences the model may have added.
            raw = raw.replace(/^```[^\n]*\n?/, '').replace(/\n?```$/, '').trim();
            this.viewerExtracted = JSON.parse(raw);
          } catch {
            this.viewerRaw = result.content || '';
          }
        }
      },
      error: () => {
        this.viewerLoading = false;
        this.viewerError = 'File not found.';
      },
    });
  }

  closeViewer(): void {
    this.viewerOpen = false;
  }
}
