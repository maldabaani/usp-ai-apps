import { ChangeDetectionStrategy, Component, computed, input, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { DiffLine, LineComment, commentKey, parseUnifiedDiff } from '../../core/diff';

/**
 * Unified diff viewer: one block per file, line numbers, added/removed coloring. With
 * `commentable`, clicking a line opens a comment box (final approval: the comments become
 * feedback for the follow-up task).
 */
@Component({
  selector: 'app-diff-view',
  imports: [FormsModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @for (file of files(); track file.path) {
      <section class="file">
        <header>
          <code>{{ file.path }}</code>
          <span class="stats"><span class="plus">+{{ file.added }}</span> <span class="minus">−{{ file.removed }}</span></span>
        </header>
        <table>
          @for (hunk of file.hunks; track $index) {
            <tr class="hunk"><td colspan="3">{{ hunk.header }}</td></tr>
            @for (line of hunk.lines; track $index) {
              <tr [class]="line.kind" [class.commentable]="commentable() && line.kind !== 'meta'"
                  (click)="open(file.path, line)" [attr.title]="commentable() ? 'Comment on this line' : null">
                <td class="no">{{ line.oldNo ?? '' }}</td>
                <td class="no">{{ line.newNo ?? '' }}</td>
                <td class="code">{{ line.text }}</td>
              </tr>
              @for (c of commentsAt(file.path, line); track c.id) {
                <tr class="comment-row">
                  <td colspan="3">
                    <div class="comment"><b>You:</b> {{ c.text }}
                      <button type="button" class="link" (click)="removed.emit(c.id)">remove</button></div>
                  </td>
                </tr>
              }
              @if (draftAt() === keyOf(file.path, line)) {
                <tr class="comment-row">
                  <td colspan="3">
                    <div class="draft">
                      <textarea rows="2" [ngModel]="draft()" (ngModelChange)="draft.set($event)" name="draft"
                                aria-label="Line comment" placeholder="What should change here?"
                                (keydown.control.enter)="save(file.path, line)"></textarea>
                      <button type="button" (click)="save(file.path, line)" [disabled]="!draft().trim()">Add</button>
                      <button type="button" class="link" (click)="draftAt.set(null)">cancel</button>
                    </div>
                  </td>
                </tr>
              }
            }
          }
        </table>
      </section>
    } @empty {
      <p>No changes.</p>
    }
  `,
  styles: `
    .file { border: 1px solid var(--dc-border); border-radius: 6px; margin-bottom: 12px; overflow: hidden; }
    header { display: flex; justify-content: space-between; background: var(--dc-code-bg); padding: 6px 8px; }
    .plus { color: var(--dc-green); } .minus { color: var(--dc-red); }
    table { border-collapse: collapse; width: 100%; font-family: ui-monospace, monospace; font-size: 12px; }
    td { padding: 0 6px; white-space: pre-wrap; word-break: break-all; vertical-align: top; }
    .no { width: 1%; color: var(--dc-text-faint); text-align: right; user-select: none; }
    .add { background: rgba(61, 220, 132, 0.12); } .del { background: rgba(255, 90, 122, 0.12); }
    .hunk td { background: rgba(77, 141, 255, 0.15); color: var(--dc-text-dim); }
    tr.commentable { cursor: pointer; }
    tr.commentable:hover td { outline: 1px solid rgba(34, 211, 238, 0.35); }
    .comment-row td { padding: 4px 8px; background: rgba(34, 211, 238, 0.06); font-family: var(--dc-sans, inherit); }
    .comment { font-size: 12.5px; white-space: pre-wrap; }
    .comment b { color: var(--dc-cyan); }
    .draft { display: flex; gap: 6px; align-items: flex-start; }
    .draft textarea { flex: 1; font: inherit; background: var(--dc-code-bg); color: inherit;
                      border: 1px solid var(--dc-border-strong); border-radius: 6px; padding: 4px 6px; }
    .draft button { font: inherit; cursor: pointer; }
    .link { background: none; border: 0; color: var(--dc-text-faint); cursor: pointer; text-decoration: underline; font-size: 11.5px; }
  `,
})
export class DiffViewComponent {
  readonly diff = input.required<string>();
  readonly commentable = input(false);
  readonly comments = input<LineComment[]>([]);
  readonly added = output<Omit<LineComment, 'id'>>();
  readonly removed = output<number>();

  readonly files = computed(() => parseUnifiedDiff(this.diff()));
  readonly draftAt = signal<string | null>(null);
  readonly draft = signal('');
  private readonly byKey = computed(() => {
    const map = new Map<string, LineComment[]>();
    for (const c of this.comments()) {
      const key = commentKey(c.path, c.line, c.side);
      map.set(key, [...(map.get(key) ?? []), c]);
    }
    return map;
  });

  keyOf(path: string, line: DiffLine): string {
    return line.newNo !== null ? commentKey(path, line.newNo, 'new') : commentKey(path, line.oldNo ?? 0, 'old');
  }

  commentsAt(path: string, line: DiffLine): LineComment[] {
    return line.kind === 'meta' ? [] : (this.byKey().get(this.keyOf(path, line)) ?? []);
  }

  open(path: string, line: DiffLine): void {
    if (!this.commentable() || line.kind === 'meta') {
      return;
    }
    const key = this.keyOf(path, line);
    if (this.draftAt() !== key) {
      this.draft.set('');
      this.draftAt.set(key);
    }
  }

  save(path: string, line: DiffLine): void {
    const text = this.draft().trim();
    if (!text) {
      return;
    }
    const side = line.newNo !== null ? 'new' : 'old';
    this.added.emit({ path, line: (side === 'new' ? line.newNo : line.oldNo) ?? 0, side, code: line.text, text });
    this.draft.set('');
    this.draftAt.set(null);
  }
}
