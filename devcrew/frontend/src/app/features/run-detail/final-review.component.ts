import { ChangeDetectionStrategy, Component, effect, inject, input, output, signal, untracked } from '@angular/core';

import { ApiService } from '../../core/api.service';
import { LineComment, commentsAsFeedback } from '../../core/diff';
import { DiffViewComponent } from './diff-view.component';

/**
 * The whole run's changes at final approval. Click a line to comment; the comments become the
 * feedback for the follow-up task when you reject ("Request changes").
 */
@Component({
  selector: 'app-final-review',
  imports: [DiffViewComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <details class="review" [open]="open()" (toggle)="onToggle($event)">
      <summary>Review the changes
        @if (comments().length) { <span class="count">{{ comments().length }} comment(s)</span> }
      </summary>
      @if (error(); as e) {
        <p class="error">{{ e }}</p>
      } @else if (diff() === null) {
        <p class="muted">Loading…</p>
      } @else {
        <p class="muted">Click a line to comment. Comments are sent as feedback when you reject
          (“Request changes”), and the crew adds a follow-up task for them.</p>
        @if (truncated()) { <p class="muted">The diff is long and was cut; see the Files tab for the rest.</p> }
        <app-diff-view [diff]="diff()!" [commentable]="true" [comments]="comments()"
                       (added)="add($event)" (removed)="remove($event)" />
      }
    </details>
  `,
  styles: `
    .review { margin: 8px 0 12px; }
    summary { cursor: pointer; font-weight: 600; color: var(--dc-cyan); }
    .count { margin-left: 8px; font-size: 12px; color: var(--dc-amber); }
    .muted { color: var(--dc-text-faint); font-size: 12.5px; }
    .error { color: var(--dc-red); }
  `,
})
export class FinalReviewComponent {
  private readonly api = inject(ApiService);
  readonly runId = input.required<string>();
  readonly feedback = output<string>();

  readonly open = signal(false);
  readonly diff = signal<string | null>(null);
  readonly truncated = signal(false);
  readonly error = signal<string | null>(null);
  readonly comments = signal<LineComment[]>([]);
  private nextId = 1;
  private loaded = false;

  constructor() {
    effect(() => {
      this.runId();
      untracked(() => {
        this.loaded = false;
        this.diff.set(null);
        this.comments.set([]);
        this.feedback.emit('');
        if (this.open()) {
          this.load();
        }
      });
    });
  }

  onToggle(event: Event): void {
    const open = (event.target as HTMLDetailsElement).open;
    this.open.set(open);
    if (open) {
      this.load();
    }
  }

  add(c: Omit<LineComment, 'id'>): void {
    this.comments.update((cs) => [...cs, { ...c, id: this.nextId++ }]);
    this.feedback.emit(commentsAsFeedback(this.comments()));
  }

  remove(id: number): void {
    this.comments.update((cs) => cs.filter((c) => c.id !== id));
    this.feedback.emit(commentsAsFeedback(this.comments()));
  }

  private load(): void {
    if (this.loaded) {
      return;
    }
    this.loaded = true;
    this.api.diff(this.runId()).subscribe({
      next: (d) => {
        this.diff.set(d.diff);
        this.truncated.set(d.truncated);
      },
      error: () => {
        this.loaded = false;
        this.error.set('Could not load the changes.');
      },
    });
  }
}
