import { DatePipe } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  computed,
  effect,
  inject,
  input,
  signal,
  untracked,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatSelectModule } from '@angular/material/select';

import { PreviewState } from '../../core/api.models';
import { ApiService } from '../../core/api.service';

const POLL_MS = 2000;

/**
 * Live preview: run the generated app in the sandbox and open it in a browser tab. The app has
 * no internet access; it is reachable from this machine only (127.0.0.1).
 */
@Component({
  selector: 'app-preview',
  imports: [DatePipe, FormsModule, MatButtonModule, MatSelectModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (state(); as s) {
      @if (!s.enabled) {
        <p class="muted">Live preview is off (it needs the Docker sandbox and PREVIEW_ENABLED=true).</p>
      } @else if (!s.options.length && s.status === 'off') {
        <p class="muted">Nothing to preview yet: the project is scaffolded after the design is approved.
          Existing repositories need <code>preview_cmd</code> and <code>preview_port</code> in
          <code>.devcrew.yaml</code>.</p>
      } @else {
        <div class="bar">
          <span class="status" [class]="'status ' + s.status">{{ statusText() }}</span>
          @if (s.options.length > 1 && !active()) {
            <mat-select class="stack" [ngModel]="stack()" (ngModelChange)="stack.set($event)" aria-label="Project">
              @for (o of s.options; track o.stack) { <mat-option [value]="o.stack">{{ o.stack }} ({{ o.path }})</mat-option> }
            </mat-select>
          }
          @if (active()) {
            <button mat-stroked-button (click)="start()" [disabled]="busy()">Restart</button>
            <button mat-stroked-button color="warn" (click)="stop()" [disabled]="busy()">Stop</button>
          } @else {
            <button mat-flat-button (click)="start()" [disabled]="busy()">Start preview</button>
          }
          @if (s.url && s.status === 'running') {
            <a mat-flat-button class="open" [href]="s.url" target="_blank" rel="noopener">Open {{ s.url }} ↗</a>
          }
        </div>
        @if (s.command) {
          <p class="muted"><code>{{ s.command }}</code>@if (s.started_at) { · started {{ s.started_at | date: 'HH:mm:ss' }} }</p>
        } @else if (selected()) {
          @let o = selected()!;
          <p class="muted">Runs <code>{{ o.command }}</code> in <code>{{ o.path }}</code> (port {{ o.port }}).</p>
        }
        @if (s.error) { <pre class="error">{{ s.error }}</pre> }
        @if (s.logs) {
          <h4>Log</h4>
          <pre class="logs">{{ s.logs }}</pre>
        }
        <p class="muted">The app runs on an internal network without internet access. The preview
          reflects the integration branch; it stops when the run ends (you can start it again).</p>
      }
    } @else if (error()) {
      <p class="error">{{ error() }}</p>
    } @else {
      <p class="muted">Loading…</p>
    }
  `,
  styles: `
    :host { display: block; padding: 12px 4px; max-width: 980px; }
    .bar { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
    .stack { width: 200px; }
    .status { font-weight: 700; font-size: 13px; padding: 2px 10px; border-radius: 12px;
              border: 1px solid var(--dc-border-strong); color: var(--dc-text-dim); }
    .status.running { color: var(--dc-teal); border-color: rgba(45, 226, 176, 0.5); }
    .status.installing, .status.starting { color: var(--dc-amber); border-color: rgba(255, 193, 77, 0.5); }
    .status.failed { color: var(--dc-red); border-color: rgba(255, 90, 122, 0.5); }
    .open { box-shadow: var(--dc-glow-teal, none); }
    .muted { color: var(--dc-text-faint); font-size: 12.5px; }
    .error { color: var(--dc-red); white-space: pre-wrap; font-size: 12px; }
    .logs { max-height: 320px; overflow: auto; font-size: 12px; background: var(--dc-code-bg); padding: 8px; border-radius: 6px; }
  `,
})
export class PreviewComponent {
  private readonly api = inject(ApiService);
  readonly runId = input.required<string>();

  readonly state = signal<PreviewState | null>(null);
  readonly error = signal<string | null>(null);
  readonly busy = signal(false);
  readonly stack = signal<string | null>(null);
  readonly active = computed(() => ['installing', 'starting', 'running'].includes(this.state()?.status ?? 'off'));
  readonly selected = computed(() => {
    const options = this.state()?.options ?? [];
    return options.find((o) => o.stack === this.stack()) ?? options[0] ?? null;
  });
  readonly statusText = computed(() => {
    switch (this.state()?.status) {
      case 'installing': return 'installing dependencies…';
      case 'starting': return 'starting…';
      case 'running': return 'running';
      case 'failed': return 'failed';
      default: return 'not running';
    }
  });
  private timer: ReturnType<typeof setTimeout> | null = null;

  constructor() {
    effect(() => {
      this.runId();
      untracked(() => this.refresh());
    });
    inject(DestroyRef).onDestroy(() => this.clearTimer());
  }

  start(): void {
    this.busy.set(true);
    this.api.startPreview(this.runId(), this.selected()?.stack).subscribe({
      next: (s) => {
        this.busy.set(false);
        this.state.update((old) => ({ ...s, options: s.options.length ? s.options : (old?.options ?? []) }));
        this.schedule();
      },
      error: (err: { error?: { detail?: unknown } }) => {
        this.busy.set(false);
        this.error.set(String(err.error?.detail ?? 'Could not start the preview.'));
        this.refresh();
      },
    });
  }

  stop(): void {
    this.busy.set(true);
    this.api.stopPreview(this.runId()).subscribe({
      next: () => {
        this.busy.set(false);
        this.refresh();
      },
      error: () => {
        this.busy.set(false);
        this.refresh();
      },
    });
  }

  refresh(): void {
    this.clearTimer();
    this.api.preview(this.runId(), true).subscribe({
      next: (s) => {
        this.state.set(s);
        this.error.set(null);
        this.schedule();
      },
      error: () => this.error.set('Could not read the preview state.'),
    });
  }

  private schedule(): void {
    this.clearTimer();
    const status = this.state()?.status;
    if (status === 'installing' || status === 'starting' || status === 'running') {
      this.timer = setTimeout(() => this.refresh(), status === 'running' ? POLL_MS * 3 : POLL_MS);
    }
  }

  private clearTimer(): void {
    if (this.timer) {
      clearTimeout(this.timer);
      this.timer = null;
    }
  }
}
