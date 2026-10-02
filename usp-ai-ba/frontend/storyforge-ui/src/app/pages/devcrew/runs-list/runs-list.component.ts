import { CommonModule } from '@angular/common';
import { Component, OnInit } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';

import { DevCrewService, RunSummary } from '../../../services/devcrew.service';

type StatusBucket = 'waiting' | 'done' | 'error' | 'active';
const WEEK_MS = 7 * 24 * 60 * 60 * 1000;

@Component({
  selector: 'app-devcrew-runs-list',
  standalone: true,
  imports: [CommonModule, RouterLink, FormsModule],
  templateUrl: './runs-list.component.html',
  styleUrl: './runs-list.component.css',
})
export class RunsListComponent implements OnInit {
  runs: RunSummary[] = [];
  loading = true;
  loadError = '';

  searchQuery = '';
  statusFilter: StatusBucket | '' = '';

  constructor(private devCrewService: DevCrewService) {}

  ngOnInit(): void {
    this.load();
  }

  load(): void {
    this.loading = true;
    this.loadError = '';
    this.devCrewService.listRuns().subscribe({
      next: (runs) => {
        this.runs = runs;
        this.loading = false;
      },
      error: () => {
        this.loadError = 'Unable to load DevCrew runs.';
        this.loading = false;
      },
    });
  }

  clearFilters(): void {
    this.searchQuery = '';
    this.statusFilter = '';
  }

  statusClass(status: string): string {
    if (status.startsWith('awaiting_') || status === 'needs_human' || status === 'paused') {
      return 'dc-status-waiting';
    }
    if (status === 'completed') return 'dc-status-done';
    if (status === 'failed' || status === 'cancelled') return 'dc-status-error';
    return 'dc-status-active';
  }

  statusBucket(status: string): StatusBucket {
    return this.statusClass(status).replace('dc-status-', '') as StatusBucket;
  }

  get filteredRuns(): RunSummary[] {
    return this.runs.filter((run) => {
      if (this.statusFilter && this.statusBucket(run.status) !== this.statusFilter) return false;
      if (this.searchQuery) {
        const q = this.searchQuery.toLowerCase();
        if (!run.request.toLowerCase().includes(q) && !run.repo_target.toLowerCase().includes(q)) {
          return false;
        }
      }
      return true;
    });
  }

  get activeCount(): number {
    return this.runs.filter((r) => this.statusBucket(r.status) === 'active').length;
  }

  get waitingCount(): number {
    return this.runs.filter((r) => this.statusBucket(r.status) === 'waiting').length;
  }

  get completedThisWeekCount(): number {
    const cutoff = Date.now() - WEEK_MS;
    return this.runs.filter(
      (r) => r.status === 'completed' && r.created_at !== null && new Date(r.created_at).getTime() >= cutoff
    ).length;
  }

  get failedCount(): number {
    return this.runs.filter((r) => this.statusBucket(r.status) === 'error').length;
  }

  relativeTime(iso: string): string {
    const diff = Date.now() - new Date(iso).getTime();
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return 'just now';
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    const days = Math.floor(hrs / 24);
    if (days < 7) return `${days}d ago`;
    return new Date(iso).toLocaleDateString();
  }

  fullDate(iso: string): string {
    return new Date(iso).toLocaleString();
  }
}
