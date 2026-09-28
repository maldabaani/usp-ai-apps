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
    .qa { border-left: 3px solid var(--dc-blue); padding: 6px 10px; margin-bottom: 10px; background: rgba(8, 17, 34, 0.6); }
    .who { font-size: 13px; color: var(--dc-text-dim); }
    .task { background: rgba(77, 141, 255, 0.18); padding: 0 6px; border-radius: 8px; margin-left: 6px; }
    .q { margin-top: 4px; } .a { color: var(--dc-teal); margin-top: 2px; }
  `,
})
export class QaPanelComponent {
  readonly entries = input.required<QAEntry[]>();
}
