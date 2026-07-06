import { CommonModule } from '@angular/common';
import { Component, OnInit } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';

import { JobSummary, StoryForgeService } from '../../services/storyforge.service';

@Component({
  selector: 'app-dashboard',
  standalone: true,
  imports: [CommonModule, RouterLink, FormsModule],
  templateUrl: './dashboard.component.html',
  styleUrl: './dashboard.component.css',
})
export class DashboardComponent implements OnInit {
  jobs: JobSummary[] = [];
  loading = true;
  loadError = '';
  rerunningId = '';

  searchQuery = '';
  systemFilter = '';
  statusFilter = '';
  selectedIds = new Set<string>();

  constructor(
    private storyForgeService: StoryForgeService,
    private router: Router
  ) {}

  ngOnInit(): void {
    this.loadJobs();
  }

  loadJobs(): void {
    this.loading = true;
    this.storyForgeService.listJobs().subscribe({
      next: (jobs) => { this.jobs = jobs; this.loading = false; },
      error: () => { this.loadError = 'Unable to load assessments.'; this.loading = false; },
    });
  }

  get uniqueSystems(): string[] {
    return [...new Set(this.jobs.map(j => j.system_name).filter(Boolean))].sort();
  }

  get inProgressCount(): number {
    return this.jobs.filter(j => !['done', 'error', 'cancelled'].includes(j.status)).length;
  }

  get filteredJobs(): JobSummary[] {
    return this.jobs.filter(job => {
      if (this.searchQuery) {
        const q = this.searchQuery.toLowerCase();
        if (!job.ppm_name.toLowerCase().includes(q) && !job.ppm_number.toLowerCase().includes(q)) {
          return false;
        }
      }
      if (this.systemFilter && job.system_name !== this.systemFilter) return false;
      if (this.statusFilter && this.statusGroup(job.status) !== this.statusFilter) return false;
      return true;
    });
  }

  statusGroup(status: string): 'done' | 'running' | 'failed' | 'cancelled' {
    if (status === 'done') return 'done';
    if (status === 'error') return 'failed';
    if (status === 'cancelled') return 'cancelled';
    return 'running';
  }

  displayStatus(status: string): string {
    if (status === 'done') return 'Done';
    if (status === 'error') return 'Failed';
    if (status === 'cancelled') return 'Cancelled';
    return 'Running';
  }

  badgeClass(status: string): string {
    return `sf-badge sf-badge-${this.statusGroup(status)}`;
  }

  toggleSelect(id: string): void {
    if (this.selectedIds.has(id)) this.selectedIds.delete(id);
    else this.selectedIds.add(id);
  }

  rerun(jobId: string): void {
    this.rerunningId = jobId;
    this.storyForgeService.rerunAssessment(jobId).subscribe({
      next: ({ job_id }) => { this.rerunningId = ''; this.router.navigate(['/status', job_id]); },
      error: () => { this.rerunningId = ''; },
    });
  }

  relativeTime(epochSeconds: number): string {
    const diff = Date.now() - epochSeconds * 1000;
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return 'just now';
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    const days = Math.floor(hrs / 24);
    if (days < 7) return `${days}d ago`;
    return new Date(epochSeconds * 1000).toLocaleDateString();
  }

  fullDate(epochSeconds: number): string {
    return new Date(epochSeconds * 1000).toLocaleString();
  }
}
