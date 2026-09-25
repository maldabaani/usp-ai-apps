import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';

import { Plan, TaskState } from '../../core/api.models';
import { dagColumns } from '../../core/dag';
import { StatusChipComponent } from '../../shared/status-chip.component';

/** Task DAG as parallel lanes: one column per wave (or planned layer), one card per task. */
@Component({
  selector: 'app-task-dag',
  imports: [StatusChipComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="columns">
      @for (col of columns(); track col.key) {
        <section class="column" [attr.aria-label]="col.title">
          <h3>{{ col.title }}</h3>
          @for (card of col.cards; track card.id) {
            <button type="button" class="card" [class.selected]="card.id === selected()"
              [class]="'card ' + (card.state?.status ?? 'pending')" (click)="taskSelected.emit(card.id)">
              <div class="top">
                <b>{{ card.id }}</b>
                <app-status-chip [status]="card.state?.status ?? 'pending'" />
              </div>
              <div class="title">{{ card.title }}</div>
              <div class="meta">
                @if (card.stack) {
                  <span>{{ card.stack }}</span>
                }
                @if (card.state?.iterations) {
                  <span>attempt {{ card.state?.iterations }}</span>
                }
                @if (card.state?.conflict_rounds) {
                  <span>conflicts {{ card.state?.conflict_rounds }}</span>
                }
              </div>
              @if (card.dependsOn.length) {
                <div class="deps">after {{ card.dependsOn.join(', ') }}</div>
              }
            </button>
          }
        </section>
      } @empty {
        <p>No tasks yet: they appear once the plan exists.</p>
      }
    </div>
  `,
  styles: `
    .columns { display: flex; gap: 12px; overflow-x: auto; padding-bottom: 8px; }
    .column { min-width: 220px; max-width: 280px; display: flex; flex-direction: column; gap: 8px; }
    h3 { margin: 0; font-size: 13px; text-transform: uppercase; color: #555; }
    .card { text-align: left; border: 1px solid #d0d7de; border-left: 4px solid #6e9eff;
            border-radius: 8px; background: #fff; padding: 8px; cursor: pointer; font: inherit; }
    .card.merged { border-left-color: #1b7f3b; }
    .card.failed, .card.blocked { border-left-color: #b3261e; }
    .card.needs_human { border-left-color: #e8a300; }
    .card.pending, .card.split { border-left-color: #b0bec5; opacity: 0.85; }
    .card.selected { outline: 2px solid #0b57d0; }
    .top { display: flex; justify-content: space-between; align-items: center; gap: 6px; }
    .title { margin: 4px 0; }
    .meta, .deps { font-size: 12px; color: #666; display: flex; gap: 8px; }
  `,
})
export class TaskDagComponent {
  readonly plan = input.required<Plan | null>();
  readonly tasks = input.required<Record<string, TaskState>>();
  readonly selected = input<string | null>(null);
  readonly taskSelected = output<string>();
  readonly columns = computed(() => dagColumns(this.plan(), this.tasks()));
}
