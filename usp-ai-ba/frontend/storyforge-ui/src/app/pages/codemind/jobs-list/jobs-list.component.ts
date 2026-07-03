import { CommonModule } from '@angular/common';
import { Component, OnInit } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';

import { CodeMindService, ExtractionJob, StartJobRequest } from '../../../services/codemind.service';

@Component({
  selector: 'app-codemind-jobs-list',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink],
  templateUrl: './jobs-list.component.html',
  styleUrl: './jobs-list.component.css',
})
export class JobsListComponent implements OnInit {
  jobs: ExtractionJob[] = [];
  loading = true;
  loadError = '';

  repositoryPath = '';
  outputDirectory = '';
  maxConcurrency: number | null = null;
  executionMode = '';
  starting = false;
  startError = '';

  constructor(
    private codeMindService: CodeMindService,
    private router: Router
  ) {}

  ngOnInit(): void {
    this.refresh();
  }

  refresh(): void {
    this.codeMindService.listJobs().subscribe({
      next: (jobs) => {
        this.jobs = jobs;
        this.loading = false;
      },
      error: () => {
        this.loadError = 'Unable to load jobs.';
        this.loading = false;
      },
    });
  }

  startJob(): void {
    if (!this.repositoryPath.trim() || this.starting) {
      return;
    }
    this.starting = true;
    this.startError = '';

    const request: StartJobRequest = { repositoryPath: this.repositoryPath.trim() };
    if (this.outputDirectory.trim()) {
      request.outputDirectory = this.outputDirectory.trim();
    }
    if (this.maxConcurrency) {
      request.maxConcurrency = this.maxConcurrency;
    }
    if (this.executionMode) {
      request.executionMode = this.executionMode;
    }

    this.codeMindService.startJob(request).subscribe({
      next: (job) => {
        this.starting = false;
        this.router.navigate(['/codemind', job.jobId]);
      },
      error: (err) => {
        this.starting = false;
        this.startError = err?.error?.detail || 'Failed to start job.';
      },
    });
  }

  deleteJob(jobId: string): void {
    if (!confirm('Delete this job and all its output files?')) {
      return;
    }
    this.codeMindService.deleteJob(jobId).subscribe({
      next: () => this.refresh(),
      error: () => alert('Delete failed.'),
    });
  }

  clearAll(): void {
    if (!confirm('Delete ALL jobs, output files, and cached data?\n\nThis cannot be undone.')) {
      return;
    }
    this.codeMindService.clearAllJobs().subscribe({
      next: () => this.refresh(),
      error: () => alert('Clear failed.'),
    });
  }

  shortId(jobId: string): string {
    return jobId.slice(0, 8);
  }
}
