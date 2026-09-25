import { ChangeDetectionStrategy, Component, input } from '@angular/core';

import { QAEntry } from '../../core/api.models';

/** Every question agents asked (each other or the human) and the answers. */
@Component({
  selector: 'app-qa-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @for (e of entries(); track e.id) {
      <div class="qa">
        <div class="who">
          <b>{{ e.asker }}</b> → <b>{{ e.target }}</b>
          @if (e.task_id) { <span class="task">{{ e.task_id }}</span> }
        </div>
        <div class="q">Q: {{ e.question }}</div>
        <div class="a">A: {{ e.answer }}</div>
      </div>
    } @empty {
      <p>No questions so far.</p>
    }
  `,
  styles: `
    .qa { border-left: 3px solid #6e9eff; padding: 6px 10px; margin-bottom: 10px; background: #fafbff; }
    .who { font-size: 13px; color: #555; }
    .task { background: #eef3ff; padding: 0 6px; border-radius: 8px; margin-left: 6px; }
    .q { margin-top: 4px; } .a { color: #135c2b; margin-top: 2px; }
  `,
})
export class QaPanelComponent {
  readonly entries = input.required<QAEntry[]>();
}
