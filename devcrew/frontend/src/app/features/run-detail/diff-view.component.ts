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
    .file { border: 1px solid var(--dc-border); border-radius: 6px; margin-bottom: 12px; overflow: hidden; }
    header { display: flex; justify-content: space-between; background: var(--dc-code-bg); padding: 6px 8px; }
    .plus { color: var(--dc-green); } .minus { color: var(--dc-red); }
    table { border-collapse: collapse; width: 100%; font-family: ui-monospace, monospace; font-size: 12px; }
    td { padding: 0 6px; white-space: pre-wrap; word-break: break-all; vertical-align: top; }
    .no { width: 1%; color: var(--dc-text-faint); text-align: right; user-select: none; }
    .add { background: rgba(61, 220, 132, 0.12); } .del { background: rgba(255, 90, 122, 0.12); }
    .hunk td { background: rgba(77, 141, 255, 0.15); color: var(--dc-text-dim); }
  `,
})
export class DiffViewComponent {
  readonly diff = input.required<string>();
  readonly files = computed(() => parseUnifiedDiff(this.diff()));
}
