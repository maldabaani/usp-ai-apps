import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

const TONE: Record<string, string> = {
  completed: 'good',
  merged: 'good',
  failed: 'bad',
  cancelled: 'muted',
  blocked: 'muted',
  split: 'muted',
  paused: 'warn',
  needs_human: 'warn',
  awaiting_plan_approval: 'warn',
  awaiting_design_approval: 'warn',
  awaiting_final_approval: 'warn',
  pending: 'muted',
};

/** Colored status badge for runs and tasks. */
@Component({
  selector: 'app-status-chip',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `<span class="chip" [class]="'chip ' + tone()">{{ label() }}</span>`,
  styles: `
    .chip { display: inline-block; padding: 1px 10px; border-radius: 12px; font-size: 12px; font-weight: 600;
            line-height: 20px; white-space: nowrap; color: var(--dc-cyan); background: rgba(62, 230, 255, 0.1);
            border: 1px solid rgba(62, 230, 255, 0.45); box-shadow: 0 0 10px rgba(62, 230, 255, 0.2); }
    .good { color: var(--dc-teal); background: rgba(45, 226, 176, 0.1); border-color: rgba(45, 226, 176, 0.45);
            box-shadow: 0 0 10px rgba(45, 226, 176, 0.2); }
    .bad { color: var(--dc-red); background: rgba(255, 90, 122, 0.1); border-color: rgba(255, 90, 122, 0.5);
           box-shadow: 0 0 10px rgba(255, 90, 122, 0.2); }
    .warn { color: var(--dc-amber); background: rgba(255, 193, 77, 0.1); border-color: rgba(255, 193, 77, 0.5);
            box-shadow: 0 0 10px rgba(255, 193, 77, 0.25); }
    .muted { color: var(--dc-text-dim); background: rgba(138, 163, 194, 0.08); border-color: rgba(138, 163, 194, 0.3);
             box-shadow: none; }
  `,
})
export class StatusChipComponent {
  readonly status = input.required<string>();
  readonly label = computed(() => this.status().replaceAll('_', ' '));
  readonly tone = computed(() => TONE[this.status()] ?? 'active');
}
