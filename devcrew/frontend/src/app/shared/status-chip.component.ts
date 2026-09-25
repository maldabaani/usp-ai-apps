import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

const TONE: Record<string, string> = {
  completed: 'good',
  merged: 'good',
  failed: 'bad',
  cancelled: 'muted',
  blocked: 'muted',
  split: 'muted',
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
    .chip { display: inline-block; padding: 1px 10px; border-radius: 12px; font-size: 12px;
            line-height: 20px; background: #dbe7ff; color: #0b3a8a; white-space: nowrap; }
    .good { background: #d7f3df; color: #135c2b; }
    .bad { background: #fbdcda; color: #8c1d18; }
    .warn { background: #ffecc2; color: #6b4a00; }
    .muted { background: #eceff1; color: #455a64; }
  `,
})
export class StatusChipComponent {
  readonly status = input.required<string>();
  readonly label = computed(() => this.status().replaceAll('_', ' '));
  readonly tone = computed(() => TONE[this.status()] ?? 'active');
}
