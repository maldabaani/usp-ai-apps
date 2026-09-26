import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatToolbarModule } from '@angular/material/toolbar';
import { RouterLink, RouterOutlet } from '@angular/router';

import { HealthReport } from './core/api.models';
import { ApiService } from './core/api.service';
import { AgentIconComponent } from './shared/agent-icon.component';

@Component({
  selector: 'app-root',
  imports: [RouterOutlet, RouterLink, MatToolbarModule, MatButtonModule, AgentIconComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <mat-toolbar color="primary" class="toolbar">
      <a routerLink="/" class="brand"><app-agent-icon kind="planner" [size]="30" [active]="true" /><span class="word">Dev<span>Crew</span></span></a>
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
    .toolbar { gap: 8px; position: sticky; top: 0; z-index: 10; background: rgba(6, 10, 20, 0.82);
               --mat-toolbar-container-background-color: rgba(6, 10, 20, 0.82); --mat-toolbar-container-text-color: var(--dc-text);
               backdrop-filter: blur(10px); border-bottom: 1px solid var(--dc-border);
               box-shadow: 0 1px 18px rgba(62, 230, 255, 0.12); color: var(--dc-text); }
    .brand { color: #f1f7ff; text-decoration: none; font-weight: 700; margin-right: 16px; display: inline-flex;
             align-items: center; gap: 8px; letter-spacing: 0.5px; }
    .brand .word span { color: var(--dc-cyan); text-shadow: 0 0 12px rgba(62, 230, 255, 0.7); }
    .spacer { flex: 1; }
    .health { font-size: 12px; font-weight: 600; padding: 2px 12px; border-radius: 12px; color: var(--dc-red);
              border: 1px solid rgba(255, 90, 122, 0.55); background: rgba(255, 90, 122, 0.1); box-shadow: var(--dc-glow-red); }
    .health.ok { color: var(--dc-teal); border-color: rgba(45, 226, 176, 0.55); background: rgba(45, 226, 176, 0.1);
                 box-shadow: var(--dc-glow-teal); }
    .content { max-width: 1600px; margin: 0 auto; padding: 16px; }
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
