import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatToolbarModule } from '@angular/material/toolbar';
import { RouterLink, RouterOutlet } from '@angular/router';

import { HealthReport } from './core/api.models';
import { ApiService } from './core/api.service';

@Component({
  selector: 'app-root',
  imports: [RouterOutlet, RouterLink, MatToolbarModule, MatButtonModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <mat-toolbar color="primary" class="toolbar">
      <a routerLink="/" class="brand">DevCrew</a>
      <a mat-button routerLink="/">Runs</a>
      <a mat-flat-button routerLink="/runs/new">New run</a>
      <span class="spacer"></span>
      @if (health(); as h) {
        <span
          class="health"
          [class.ok]="h.ok"
          [title]="healthDetail()"
          tabindex="0"
          aria-label="Backend health"
          >{{ h.ok ? 'healthy' : 'degraded' }}</span
        >
      } @else if (healthError()) {
        <span class="health" [title]="healthError() ?? ''" tabindex="0">backend unreachable</span>
      }
    </mat-toolbar>
    <main class="content">
      <router-outlet />
    </main>
  `,
  styles: `
    .toolbar { gap: 8px; position: sticky; top: 0; z-index: 10; }
    .brand { color: inherit; text-decoration: none; font-weight: 600; margin-right: 16px; }
    .spacer { flex: 1; }
    .health { font-size: 13px; padding: 2px 10px; border-radius: 12px; background: #b3261e; color: #fff; }
    .health.ok { background: #1b7f3b; }
    .content { max-width: 1400px; margin: 0 auto; padding: 16px; }
  `,
})
export class AppComponent {
  private readonly api = inject(ApiService);
  readonly health = signal<HealthReport | null>(null);
  readonly healthError = signal<string | null>(null);

  constructor() {
    this.api.health().subscribe({
      next: (report) => this.health.set(report),
      error: (err: { status?: number; error?: HealthReport }) => {
        if (err.status === 503 && err.error) {
          this.health.set(err.error); // degraded, but reachable
        } else {
          this.healthError.set(`Cannot reach ${this.api.baseUrl}`);
        }
      },
    });
  }

  healthDetail(): string {
    return (this.health()?.checks ?? [])
      .map((c) => `${c.ok ? '✓' : '✗'} ${c.name}: ${c.detail}`)
      .join('\n');
  }
}
