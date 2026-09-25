import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

import { parseUnifiedDiff } from '../../core/diff';

/** Unified diff viewer: one block per file, line numbers, added/removed coloring. */
@Component({
  selector: 'app-diff-view',
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
              <tr [class]="line.kind">
                <td class="no">{{ line.oldNo ?? '' }}</td>
                <td class="no">{{ line.newNo ?? '' }}</td>
                <td class="code">{{ line.text }}</td>
              </tr>
            }
          }
        </table>
      </section>
    } @empty {
      <p>No changes.</p>
    }
  `,
  styles: `
    .file { border: 1px solid #d0d7de; border-radius: 6px; margin-bottom: 12px; overflow: hidden; }
    header { display: flex; justify-content: space-between; background: #f6f8fa; padding: 6px 8px; }
    .plus { color: #1b7f3b; } .minus { color: #b3261e; }
    table { border-collapse: collapse; width: 100%; font-family: ui-monospace, monospace; font-size: 12px; }
    td { padding: 0 6px; white-space: pre-wrap; word-break: break-all; vertical-align: top; }
    .no { width: 1%; color: #999; text-align: right; user-select: none; }
    .add { background: #e6ffec; } .del { background: #ffebe9; }
    .hunk td { background: #ddf4ff; color: #555; }
  `,
})
export class DiffViewComponent {
  readonly diff = input.required<string>();
  readonly files = computed(() => parseUnifiedDiff(this.diff()));
}
