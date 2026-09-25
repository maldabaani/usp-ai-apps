import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  computed,
  effect,
  inject,
  input,
  signal,
  untracked,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';

import { PlanTask, TaskState } from '../../core/api.models';
import { ApiService } from '../../core/api.service';
import { StatusChipComponent } from '../../shared/status-chip.component';
import { DiffViewComponent } from './diff-view.component';

/** One task: status, review, test results, feedback and its diff. */
@Component({
  selector: 'app-task-detail',
  imports: [StatusChipComponent, DiffViewComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (task(); as t) {
      <h3>{{ t.id }} · {{ t.title }} <app-status-chip [status]="state()?.status ?? 'pending'" /></h3>
      <p class="desc">{{ t.description }}</p>
      @if (state(); as s) {
        <p class="meta">
          attempts {{ s.iterations }}
          @if (s.branch) { · branch <code>{{ s.branch }}</code> }
          @if (s.commit) { · commit <code>{{ s.commit.slice(0, 10) }}</code> }
          @if (s.wave) { · wave {{ s.wave }}, lane {{ (s.lane ?? 0) + 1 }} }
        </p>
        @if (s.error) {
          <p class="error">{{ s.error }}</p>
        }
        @if (s.feedback) {
          <h4>Feedback for the developer</h4>
          <pre>{{ s.feedback }}</pre>
        }
        @if (s.review; as r) {
          <h4>Review: {{ r.decision.replace('_', ' ') }}</h4>
          @if (r.summary) { <p>{{ r.summary }}</p> }
          @if (r.issues.length) {
            <table class="issues">
              <thead><tr><th>Severity</th><th>Where</th><th>Rule</th><th>Issue</th></tr></thead>
              <tbody>
                @for (i of r.issues; track $index) {
                  <tr [class]="i.severity">
                    <td>{{ i.severity }}</td>
                    <td><code>{{ i.file }}{{ i.line ? ':' + i.line : '' }}</code></td>
                    <td>{{ i.rule_ref ?? '' }}</td>
                    <td>{{ i.message }}</td>
                  </tr>
                }
              </tbody>
            </table>
          }
        }
        @if (s.test_results; as tr) {
          <h4>Tests: {{ !tr.ran ? 'not run' : tr.passed ? 'passed' : 'failed' }}
            @if (tr.command) { <code>{{ tr.command }}</code> }</h4>
          @if (tr.failed.length) {
            <ul>@for (f of tr.failed; track f) { <li><code>{{ f }}</code></li> }</ul>
          }
          @if (tr.logs_excerpt) { <pre class="logs">{{ tr.logs_excerpt }}</pre> }
        }
      }
      <h4>Changes</h4>
      @if (diffError(); as e) {
        <p class="error">{{ e }}</p>
      } @else {
        <app-diff-view [diff]="diff()" />
      }
    } @else {
      <p>Select a task to see its review, tests and diff.</p>
    }
  `,
  styles: `
    .desc { color: #444; }
    .meta { color: #666; font-size: 13px; }
    pre { white-space: pre-wrap; background: #f6f8fa; padding: 8px; border-radius: 6px; font-size: 12px; }
    .logs { max-height: 300px; overflow: auto; }
    .issues { border-collapse: collapse; width: 100%; font-size: 13px; }
    .issues th, .issues td { border-bottom: 1px solid #eee; padding: 4px 6px; text-align: left; }
    .blocker td:first-child, .major td:first-child { color: #b3261e; font-weight: 600; }
    .error { color: #b3261e; }
  `,
})
export class TaskDetailComponent {
  private readonly api = inject(ApiService);
  private readonly destroyRef = inject(DestroyRef);
  readonly runId = input.required<string>();
  readonly task = input.required<PlanTask | null>();
  readonly state = input.required<TaskState | null>();
  readonly diff = signal('');
  readonly diffError = signal<string | null>(null);

  /** Changes only when the diff can have changed (not on every run refresh). */
  private readonly diffKey = computed(() => {
    const task = this.task();
    const state = this.state();
    return task && state?.branch
      ? `${task.id}|${state.commit}|${state.iterations}|${state.status}`
      : '';
  });

  constructor() {
    effect(() => {
      const key = this.diffKey();
      untracked(() => (key ? this.loadDiff(key.split('|')[0]) : this.diff.set('')));
    });
  }

  private loadDiff(taskId: string): void {
    this.api
      .diff(this.runId(), taskId)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (d) => {
          this.diff.set(d.diff);
          this.diffError.set(null);
        },
        error: () => this.diffError.set('Could not load the diff.'),
      });
  }
}
