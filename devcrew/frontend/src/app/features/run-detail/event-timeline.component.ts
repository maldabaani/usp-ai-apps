import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, computed, input, signal } from '@angular/core';
import { MatSlideToggleModule } from '@angular/material/slide-toggle';

import { RunEvent } from '../../core/api.models';

const NOISY = new Set(['tool_call', 'tool_result', 'node_finished']);

export function describeEvent(e: RunEvent): string {
  const p = e.payload;
  const str = (v: unknown): string => (typeof v === 'string' ? v : JSON.stringify(v ?? ''));
  switch (e.type) {
    case 'node_started':
      return `${e.node} started`;
    case 'node_finished':
      return `${e.node} finished`;
    case 'tool_call':
      return `${str(p['tool'])}(${str(p['args']).slice(0, 120)})`;
    case 'tool_result':
      return `${str(p['tool'])} → ${p['ok'] === false ? 'failed: ' : ''}${str(p['result']).slice(0, 160)}`;
    case 'question':
      return `${str(p['asker'])} asks ${str(p['target'])}: ${str(p['question'])}`;
    case 'answer':
      return `answer: ${str(p['answer'])}`;
    case 'merge':
      return `merged ${e.task_id} into ${str(p['into'])}`;
    case 'error':
      return str(p['message']);
    case 'status':
      return `status → ${str(p['status']).replaceAll('_', ' ')}`;
    case 'awaiting_input':
      return `waiting for you: ${str(p['title'])}`;
    default:
      return e.type;
  }
}

/** Live event timeline (newest first). */
@Component({
  selector: 'app-event-timeline',
  imports: [DatePipe, MatSlideToggleModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="bar">
      <mat-slide-toggle [checked]="showAll()" (change)="showAll.set($event.checked)">
        Show tool calls
      </mat-slide-toggle>
      <span class="count">{{ visible().length }} events</span>
    </div>
    <ol class="timeline">
      @for (e of visible(); track e.id) {
        <li [class]="'row ' + e.type">
          <span class="time">{{ e.created_at | date: 'HH:mm:ss' }}</span>
          <span class="type">{{ e.type.replace('_', ' ') }}</span>
          @if (e.task_id) {
            <span class="task">{{ e.task_id }}</span>
          }
          <span class="text">{{ describe(e) }}</span>
        </li>
      } @empty {
        <li class="empty">Waiting for events…</li>
      }
    </ol>
  `,
  styles: `
    .bar { display: flex; align-items: center; gap: 16px; margin-bottom: 8px; }
    .count { color: var(--dc-text-dim); font-size: 13px; }
    .timeline { list-style: none; margin: 0; padding: 0; max-height: 70vh; overflow: auto;
                font-family: ui-monospace, monospace; font-size: 13px; }
    .row { display: flex; gap: 10px; padding: 3px 6px; border-bottom: 1px solid var(--dc-border); }
    .time { color: var(--dc-text-faint); min-width: 64px; }
    .type { min-width: 110px; color: var(--dc-text-dim); }
    .task { background: rgba(77, 141, 255, 0.18); padding: 0 6px; border-radius: 8px; }
    .text { white-space: pre-wrap; word-break: break-word; }
    .error .text { color: var(--dc-red); }
    .awaiting_input { background: rgba(255, 193, 77, 0.1); }
    .merge .text { color: var(--dc-teal); }
    .empty { color: var(--dc-text-faint); padding: 8px; }
  `,
})
export class EventTimelineComponent {
  readonly events = input.required<RunEvent[]>();
  readonly showAll = signal(false);
  readonly visible = computed(() =>
    this.events()
      .filter((e) => this.showAll() || !NOISY.has(e.type) || e.payload['ok'] === false)
      .slice()
      .reverse(),
  );
  readonly describe = describeEvent;
}
