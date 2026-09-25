import { DatePipe, SlicePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, DestroyRef, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatProgressBarModule } from '@angular/material/progress-bar';
import { MatTableModule } from '@angular/material/table';
import { RouterLink } from '@angular/router';
import { switchMap, timer } from 'rxjs';

import { RunSummary } from '../../core/api.models';
import { ApiService } from '../../core/api.service';
import { StatusChipComponent } from '../../shared/status-chip.component';

const REFRESH_MS = 5000;

@Component({
  selector: 'app-runs-list',
  imports: [
    RouterLink, DatePipe, SlicePipe, MatTableModule, MatButtonModule, MatCardModule,
    MatProgressBarModule, StatusChipComponent,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="header">
      <h1>Runs</h1>
      <a mat-flat-button routerLink="/runs/new">New run</a>
    </div>
    @if (loading()) {
      <mat-progress-bar mode="indeterminate" />
    }
    @if (error(); as e) {
      <p class="error" role="alert">{{ e }}</p>
    }
    @if (runs().length === 0 && !loading()) {
      <mat-card><mat-card-content>No runs yet. Start one with <b>New run</b>.</mat-card-content></mat-card>
    } @else {
      <table mat-table [dataSource]="runs()" class="runs">
        <ng-container matColumnDef="request">
          <th mat-header-cell *matHeaderCellDef>Request</th>
          <td mat-cell *matCellDef="let run">
            <a [routerLink]="['/runs', run.id]">{{ run.request | slice: 0 : 90 }}{{ run.request.length > 90 ? '…' : '' }}</a>
          </td>
        </ng-container>
        <ng-container matColumnDef="repo">
          <th mat-header-cell *matHeaderCellDef>Repository</th>
          <td mat-cell *matCellDef="let run">{{ run.repo_target }}</td>
        </ng-container>
        <ng-container matColumnDef="status">
          <th mat-header-cell *matHeaderCellDef>Status</th>
          <td mat-cell *matCellDef="let run"><app-status-chip [status]="run.status" /></td>
        </ng-container>
        <ng-container matColumnDef="pr">
          <th mat-header-cell *matHeaderCellDef>PR</th>
          <td mat-cell *matCellDef="let run">
            @if (run.pr_url) {
              <a [href]="run.pr_url" target="_blank" rel="noopener">open</a>
            }
          </td>
        </ng-container>
        <ng-container matColumnDef="created">
          <th mat-header-cell *matHeaderCellDef>Created</th>
          <td mat-cell *matCellDef="let run">{{ run.created_at | date: 'short' }}</td>
        </ng-container>
        <tr mat-header-row *matHeaderRowDef="columns"></tr>
        <tr mat-row *matRowDef="let row; columns: columns"></tr>
      </table>
    }
  `,
  styles: `
    .header { display: flex; align-items: center; justify-content: space-between; }
    .runs { width: 100%; }
    .error { color: #b3261e; }
  `,
})
export class RunsListComponent {
  private readonly api = inject(ApiService);
  readonly runs = signal<RunSummary[]>([]);
  readonly loading = signal(true);
  readonly error = signal<string | null>(null);
  readonly columns = ['request', 'repo', 'status', 'pr', 'created'];

  constructor() {
    timer(0, REFRESH_MS)
      .pipe(
        switchMap(() => this.api.listRuns()),
        takeUntilDestroyed(inject(DestroyRef)),
      )
      .subscribe({
        next: (runs) => {
          this.runs.set(runs);
          this.loading.set(false);
          this.error.set(null);
        },
        error: () => {
          this.loading.set(false);
          this.error.set(`Cannot reach the backend at ${this.api.baseUrl}.`);
        },
      });
  }
}
