import { CommonModule } from '@angular/common';
import { Component, OnInit } from '@angular/core';
import { RouterLink } from '@angular/router';

import { DevCrewService, RunSummary } from '../../../services/devcrew.service';

@Component({
  selector: 'app-devcrew-runs-list',
  standalone: true,
  imports: [CommonModule, RouterLink],
  templateUrl: './runs-list.component.html',
  styleUrl: './runs-list.component.css',
})
export class RunsListComponent implements OnInit {
  runs: RunSummary[] = [];
  loading = true;
  loadError = '';

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

  statusClass(status: string): string {
    if (status.startsWith('awaiting_') || status === 'needs_human' || status === 'paused') {
      return 'dc-status-waiting';
    }
    if (status === 'completed') return 'dc-status-done';
    if (status === 'failed' || status === 'cancelled') return 'dc-status-error';
    return 'dc-status-active';
  }
}
