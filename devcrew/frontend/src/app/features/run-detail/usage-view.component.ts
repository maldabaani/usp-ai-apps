import { DecimalPipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

import { RunUsage, UsageLine } from '../../core/api.models';
import { formatDuration } from '../../core/workflow-layout';
import { AgentIconComponent } from '../../shared/agent-icon.component';

/** Tokens and time of a run: totals, budget, per role and per task (local models: no cost). */
@Component({
  selector: 'app-usage-view',
  imports: [DecimalPipe, AgentIconComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (usage(); as u) {
      <div class="cards">
        <div class="card"><span class="label">Tokens</span><b>{{ u.total_tokens | number }}</b>
          <span class="sub">{{ u.input_tokens | number }} in · {{ u.output_tokens | number }} out</span></div>
        <div class="card"><span class="label">Model calls</span><b>{{ u.calls | number }}</b>
          <span class="sub">{{ duration(u.model_seconds) }} of model time</span></div>
        <div class="card"><span class="label">Working time</span><b>{{ duration(u.active_s) }}</b>
          <span class="sub">{{ duration(u.elapsed_s) }} elapsed</span></div>
        <div class="card"><span class="label">Waiting for you</span><b>{{ duration(u.waiting_s) }}</b>
          <span class="sub">approvals, questions, pauses</span></div>
      </div>
      @if (u.budget; as b) {
        <div class="budget">
          @if (b.tokens) {
            <div class="meter"><span>Tokens {{ u.total_tokens | number }} / {{ b.tokens | number }}</span>
              <div class="bar"><div [style.width.%]="percent(u.total_tokens, b.tokens)" [class.over]="u.total_tokens >= b.tokens"></div></div></div>
          }
          @if (b.minutes) {
            <div class="meter"><span>Working minutes {{ u.active_s / 60 | number: '1.0-1' }} / {{ b.minutes }}</span>
              <div class="bar"><div [style.width.%]="percent(u.active_s / 60, b.minutes)" [class.over]="u.active_s / 60 >= b.minutes"></div></div></div>
          }
          <p class="hint">Checked before every wave of tasks: at the limit the run asks you to continue or stop.</p>
        </div>
      } @else {
        <p class="hint">No budget for this run (set one on New run or with RUN_TOKEN_BUDGET / RUN_TIME_BUDGET_MIN).</p>
      }
      <div class="tables">
        <table>
          <thead><tr><th>Role</th><th>Calls</th><th>Tokens</th><th>Model time</th></tr></thead>
          <tbody>
            @for (r of u.by_role; track r.key) {
              <tr>
                <td class="role"><app-agent-icon [kind]="r.key" [size]="20" />{{ r.key }}</td>
                <td>{{ r.calls }}</td><td>{{ total(r) | number }}
                  <span class="share">{{ percent(total(r), u.total_tokens) | number: '1.0-0' }}%</span></td>
                <td>{{ duration(r.model_seconds) }}</td>
              </tr>
            } @empty { <tr><td colspan="4" class="hint">No model calls yet.</td></tr> }
          </tbody>
        </table>
        <table>
          <thead><tr><th>Task</th><th>Calls</th><th>Tokens</th><th>Model time</th></tr></thead>
          <tbody>
            @for (t of tasks(); track t.key) {
              <tr><td>{{ t.key }}</td><td>{{ t.calls }}</td><td>{{ total(t) | number }}</td><td>{{ duration(t.model_seconds) }}</td></tr>
            } @empty { <tr><td colspan="4" class="hint">No task has run yet.</td></tr> }
          </tbody>
        </table>
      </div>
    } @else {
      <p class="hint">Loading usage…</p>
    }
  `,
  styles: `
    :host { display: block; padding: 12px 4px; }
    .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 10px; }
    .card { display: flex; flex-direction: column; gap: 2px; padding: 10px 12px; border-radius: 10px;
            border: 1px solid var(--dc-border); background: rgba(8, 17, 34, 0.7); }
    .card b { font-size: 22px; color: var(--dc-cyan); }
    .label { font-size: 12px; color: var(--dc-text-dim); text-transform: uppercase; letter-spacing: 0.6px; }
    .sub, .hint { font-size: 12px; color: var(--dc-text-faint); }
    .budget { margin: 14px 0 4px; display: flex; flex-direction: column; gap: 8px; max-width: 640px; }
    .meter span { font-size: 13px; }
    .bar { height: 8px; border-radius: 4px; background: rgba(138, 163, 194, 0.15); overflow: hidden; margin-top: 4px; }
    .bar div { height: 100%; background: var(--dc-teal); box-shadow: var(--dc-glow-teal); max-width: 100%; }
    .bar div.over { background: var(--dc-red); }
    .tables { display: grid; grid-template-columns: repeat(auto-fit, minmax(360px, 1fr)); gap: 16px; margin-top: 14px; }
    table { border-collapse: collapse; width: 100%; font-size: 13px; }
    th, td { text-align: left; padding: 5px 8px; border-bottom: 1px solid var(--dc-border); }
    th { color: var(--dc-text-dim); font-weight: 600; }
    .role { display: flex; align-items: center; gap: 6px; text-transform: capitalize; }
    .share { color: var(--dc-text-faint); font-size: 11px; margin-left: 4px; }
  `,
})
export class UsageViewComponent {
  readonly usage = input<RunUsage | null>(null);
  readonly tasks = computed(() => this.usage()?.by_task ?? []);

  total(line: UsageLine): number {
    return line.input_tokens + line.output_tokens;
  }

  percent(value: number, of: number): number {
    return of > 0 ? Math.min(100, (value / of) * 100) : 0;
  }

  duration(seconds: number): string {
    return formatDuration(seconds * 1000);
  }
}
