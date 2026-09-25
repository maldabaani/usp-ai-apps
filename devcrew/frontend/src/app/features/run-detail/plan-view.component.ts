import { ChangeDetectionStrategy, Component, input } from '@angular/core';

import { Plan } from '../../core/api.models';

@Component({
  selector: 'app-plan-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (plan(); as plan) {
      <p class="summary">{{ plan.summary }}</p>
      <h3>User stories</h3>
      @for (story of plan.user_stories; track story.id) {
        <div class="story">
          <b>{{ story.id }}</b> {{ story.story }}
          <ul>
            @for (ac of story.acceptance_criteria; track $index) {
              <li>{{ ac }}</li>
            }
          </ul>
        </div>
      }
      <h3>Tasks</h3>
      <table class="tasks">
        <thead><tr><th>ID</th><th>Title</th><th>Stack</th><th>Depends on</th><th>Files</th></tr></thead>
        <tbody>
          @for (task of plan.tasks; track task.id) {
            <tr>
              <td><b>{{ task.id }}</b></td>
              <td>{{ task.title }}<div class="desc">{{ task.description }}</div></td>
              <td>{{ task.stack }}</td>
              <td>{{ task.depends_on.join(', ') || '—' }}</td>
              <td><code>{{ task.target_files.join(', ') }}</code></td>
            </tr>
          }
        </tbody>
      </table>
    } @else {
      <p>The Planner has not produced a plan yet.</p>
    }
  `,
  styles: `
    .summary { font-size: 15px; }
    .story { margin-bottom: 8px; }
    .tasks { border-collapse: collapse; width: 100%; font-size: 14px; }
    .tasks th, .tasks td { border-bottom: 1px solid #eee; padding: 6px; text-align: left; vertical-align: top; }
    .desc { color: #666; font-size: 13px; }
  `,
})
export class PlanViewComponent {
  readonly plan = input.required<Plan | null>();
}
