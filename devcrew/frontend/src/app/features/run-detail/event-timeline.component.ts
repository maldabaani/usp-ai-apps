import { ScrollingModule } from '@angular/cdk/scrolling';
import { DatePipe, JsonPipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, computed, input, signal } from '@angular/core';
import { MatSlideToggleModule } from '@angular/material/slide-toggle';

import { RunEvent } from '../../core/api.models';

const NOISY = new Set(['tool_call', 'tool_result', 'node_finished', 'llm_usage']);

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
    case 'message': {
      const target = e.task_id ? ` (to ${e.task_id})` : '';
      return p['reply'] ? `${str(p['status'])}${target}: ${str(p['reply'])}` : `you${target}: ${str(p['text'])}`;
    }
    case 'llm_usage':
      return `${str(p['role'])}: ${Number(p['input_tokens'] ?? 0) + Number(p['output_tokens'] ?? 0)} tokens in ${(Number(p['duration_ms'] ?? 0) / 1000).toFixed(1)}s`;
    case 'awaiting_input':
      return p['kind'] === 'watch'
        ? 'watching the pull request for reviews, CI and conflicts'
        : `waiting for you: ${str(p['title'])}`;
    default:
      return e.type;
  }
}

const ROW_PX = 26;
const MAX_HEIGHT_PX = 560;

/**
 * Live event timeline (newest first). Rows are virtualized (long runs have tens of thousands of
 * events); click a row for its full text.
 */
@Component({
  selector: 'app-event-timeline',
  imports: [DatePipe, JsonPipe, MatSlideToggleModule, ScrollingModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="bar">
      <mat-slide-toggle [checked]="showAll()" (change)="showAll.set($event.checked)">
        Show tool calls
      </mat-slide-toggle>
      <input class="filter" type="search" placeholder="Filter (text, node, task)" aria-label="Filter events"
             [value]="filter()" (input)="filter.set($any($event.target).value)" />
      <span class="count">{{ visible().length }} events</span>
    </div>
    @if (visible().length) {
      <cdk-virtual-scroll-viewport class="timeline" [itemSize]="rowPx" [style.height.px]="height()" [minBufferPx]="400" [maxBufferPx]="800">
        <div *cdkVirtualFor="let e of visible(); trackBy: trackById" [class]="'row ' + e.type"
             [class.selected]="selected()?.id === e.id" (click)="toggle(e)" (keydown.enter)="toggle(e)"
             tabindex="0" role="button" [title]="describe(e)">
          <span class="time">{{ e.created_at | date: 'HH:mm:ss' }}</span>
          <span class="type">{{ e.type.replace('_', ' ') }}</span>
          @if (e.task_id) {
            <span class="task">{{ e.task_id }}</span>
          }
          <span class="text">{{ describe(e) }}</span>
        </div>
      </cdk-virtual-scroll-viewport>
    } @else {
      <p class="empty">{{ events().length ? 'No event matches the filter.' : 'Waiting for events…' }}</p>
    }
    @if (selected(); as e) {
      <section class="detail">
        <header><b>{{ e.type }}</b> · {{ e.created_at | date: 'HH:mm:ss.SSS' }}@if (e.node) { · {{ e.node }} }
          @if (e.task_id) { · {{ e.task_id }} }
          <button type="button" class="close" (click)="selected.set(null)" aria-label="Close">✕</button></header>
        <pre>{{ describe(e) }}</pre>
        <details><summary>Payload</summary><pre>{{ e.payload | json }}</pre></details>
      </section>
    }
  `,
  styles: `
    .bar { display: flex; align-items: center; gap: 16px; margin-bottom: 8px; flex-wrap: wrap; }
    .count { color: var(--dc-text-dim); font-size: 13px; }
    .filter { font: inherit; font-size: 13px; padding: 4px 8px; border-radius: 6px; width: 240px;
              background: var(--dc-code-bg); color: inherit; border: 1px solid var(--dc-border-strong); }
    .timeline { font-family: ui-monospace, monospace; font-size: 13px; }
    .row { display: flex; gap: 10px; padding: 0 6px; height: 26px; line-height: 26px; box-sizing: border-box;
           border-bottom: 1px solid var(--dc-border); cursor: pointer; overflow: hidden; }
    .row:hover { background: rgba(62, 230, 255, 0.05); }
    .row.selected { background: rgba(62, 230, 255, 0.12); }
    .time { color: var(--dc-text-faint); min-width: 64px; flex: none; }
    .type { min-width: 110px; color: var(--dc-text-dim); flex: none; }
    .task { background: rgba(77, 141, 255, 0.18); padding: 0 6px; border-radius: 8px; flex: none; height: 20px;
            line-height: 20px; margin-top: 3px; }
    .text { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-width: 0; }
    .error .text { color: var(--dc-red); }
    .awaiting_input { background: rgba(255, 193, 77, 0.1); }
    .merge .text { color: var(--dc-teal); }
    .empty { color: var(--dc-text-faint); padding: 8px; }
    .detail { margin-top: 8px; padding: 8px 10px; border: 1px solid var(--dc-border-strong); border-radius: 8px;
              font-size: 12.5px; }
    .detail header { display: flex; gap: 6px; align-items: center; }
    .detail .close { margin-left: auto; background: none; border: 0; color: var(--dc-text-dim); cursor: pointer; }
    .detail pre { white-space: pre-wrap; word-break: break-word; max-height: 260px; overflow: auto; margin: 6px 0 0; }
  `,
})
export class EventTimelineComponent {
  readonly events = input.required<RunEvent[]>();
  readonly showAll = signal(false);
  readonly filter = signal('');
  readonly selected = signal<RunEvent | null>(null);
  readonly rowPx = ROW_PX;
  readonly visible = computed(() => {
    const needle = this.filter().trim().toLowerCase();
    return this.events()
      .filter((e) => this.showAll() || !NOISY.has(e.type) || e.payload['ok'] === false)
      .filter(
        (e) =>
          !needle ||
          describeEvent(e).toLowerCase().includes(needle) ||
          (e.node ?? '').toLowerCase().includes(needle) ||
          (e.task_id ?? '').toLowerCase().includes(needle),
      )
      .slice()
      .reverse();
  });
  readonly describe = describeEvent;
  /** As tall as its rows, up to MAX_HEIGHT_PX (then it scrolls). */
  readonly height = computed(() => Math.min(this.visible().length * ROW_PX + 2, MAX_HEIGHT_PX));

  trackById(_: number, e: RunEvent): number {
    return e.id;
  }

  toggle(e: RunEvent): void {
    this.selected.set(this.selected()?.id === e.id ? null : e);
  }
}
