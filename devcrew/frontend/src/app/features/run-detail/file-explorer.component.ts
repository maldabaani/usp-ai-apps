import { ChangeDetectionStrategy, Component, DestroyRef, computed, effect, inject, input, signal, untracked } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatSelectModule } from '@angular/material/select';

import { FileContent, FileEntry, TaskState } from '../../core/api.models';
import { ApiService } from '../../core/api.service';

/** Browse the run's workspace (integration branch, main, or a task branch) from git. */
@Component({
  selector: 'app-file-explorer',
  imports: [FormsModule, MatFormFieldModule, MatSelectModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <mat-form-field appearance="outline" class="ref">
      <mat-label>Branch</mat-label>
      <mat-select [ngModel]="ref()" (ngModelChange)="ref.set($event)">
        @for (r of refs(); track r.value) {
          <mat-option [value]="r.value">{{ r.label }}</mat-option>
        }
      </mat-select>
    </mat-form-field>
    @if (error(); as e) {
      <p class="error">{{ e }}</p>
    }
    <div class="explorer">
      <nav class="tree" aria-label="Files">
        @for (f of files(); track f.path) {
          <button type="button" class="file" [class.active]="f.path === content()?.path" (click)="open(f.path)"
            [style.padding-left.px]="8 + depth(f) * 12">
            {{ name(f) }}
          </button>
        } @empty {
          <p class="muted">No files yet.</p>
        }
      </nav>
      <section class="viewer">
        @if (content(); as c) {
          <header><code>{{ c.path }}</code> on <code>{{ c.ref }}</code></header>
          @if (c.binary) {
            <p class="muted">Binary file.</p>
          } @else {
            <pre>{{ c.content }}</pre>
            @if (c.truncated) { <p class="muted">Truncated.</p> }
          }
        } @else {
          <p class="muted">Select a file.</p>
        }
      </section>
    </div>
  `,
  styles: `
    .ref { width: 360px; }
    .explorer { display: grid; grid-template-columns: 320px 1fr; gap: 12px; min-height: 400px; }
    .tree { max-height: 70vh; overflow: auto; border: 1px solid var(--dc-border); border-radius: 6px; }
    .file { display: block; width: 100%; text-align: left; background: none; border: 0; padding: 3px 8px;
            font: 13px ui-monospace, monospace; cursor: pointer; }
    .file:hover { background: var(--dc-panel-hover); } .file.active { background: rgba(62, 230, 255, 0.15); }
    .viewer { border: 1px solid var(--dc-border); border-radius: 6px; padding: 8px; overflow: auto; max-height: 70vh; }
    pre { margin: 0; font-size: 12px; white-space: pre; }
    .muted { color: var(--dc-text-faint); } .error { color: var(--dc-red); }
  `,
})
export class FileExplorerComponent {
  private readonly api = inject(ApiService);
  private readonly destroyRef = inject(DestroyRef);
  readonly runId = input.required<string>();
  readonly tasks = input<Record<string, TaskState>>({});
  readonly ref = signal('integration');
  readonly files = signal<FileEntry[]>([]);
  readonly content = signal<FileContent | null>(null);
  readonly error = signal<string | null>(null);

  readonly refs = computed(() => [
    { value: 'integration', label: 'integration branch' },
    { value: 'main', label: 'main (scaffold)' },
    ...Object.values(this.tasks())
      .filter((t) => t.branch)
      .map((t) => ({ value: `task:${t.id}`, label: `task ${t.id}` })),
  ]);

  constructor() {
    effect(() => {
      const runId = this.runId();
      const ref = this.ref();
      untracked(() => this.load(runId, ref));
    });
  }

  depth(f: FileEntry): number {
    return f.path.split('/').length - 1;
  }

  name(f: FileEntry): string {
    return f.path;
  }

  open(path: string): void {
    this.api
      .readFile(this.runId(), path, this.ref())
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({ next: (c) => this.content.set(c), error: () => this.error.set(`Cannot open ${path}.`) });
  }

  private load(runId: string, ref: string): void {
    this.content.set(null);
    this.api
      .listFiles(runId, ref)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (list) => {
          this.files.set(list.files);
          this.error.set(null);
        },
        error: (err: { status?: number }) => {
          this.files.set([]);
          this.error.set(err.status === 404 ? 'The workspace is created after the design is approved.' : 'Cannot list files.');
        },
      });
  }
}
