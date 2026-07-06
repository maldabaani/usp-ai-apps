import { CommonModule } from '@angular/common';
import { Component, OnInit } from '@angular/core';

import { ErrorRecord, MonitoringService } from '../../services/monitoring.service';

@Component({
  selector: 'app-monitoring',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './monitoring.component.html',
  styleUrl: './monitoring.component.css',
})
export class MonitoringComponent implements OnInit {
  loading = true;
  loadError = '';
  errors: ErrorRecord[] = [];
  expandedIndexes: Record<number, boolean> = {};

  constructor(private monitoringService: MonitoringService) {}

  ngOnInit(): void {
    this.load();
  }

  load(): void {
    this.loading = true;
    this.loadError = '';
    this.monitoringService.getErrors().subscribe({
      next: (errors) => {
        this.errors = errors;
        this.loading = false;
      },
      error: () => {
        this.loadError = 'Unable to load monitoring data.';
        this.loading = false;
      },
    });
  }

  toggleTraceback(index: number): void {
    this.expandedIndexes[index] = !this.expandedIndexes[index];
  }

  formatTimestamp(ms: number): string {
    return new Date(ms).toLocaleString();
  }
}
