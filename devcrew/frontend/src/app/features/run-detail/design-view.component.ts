import { ChangeDetectionStrategy, Component, input } from '@angular/core';

import { Design } from '../../core/api.models';
import { MarkdownPipe } from '../../shared/markdown.pipe';

@Component({
  selector: 'app-design-view',
  imports: [MarkdownPipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (design(); as d) {
      <p>
        Stack <b>{{ d.stack }}</b> · template <code>{{ d.template_id }}</code>
        @for (c of d.components; track c.path) {
          · <code>{{ c.template_id }}</code> in <code>{{ c.path }}/</code>
        }
      </p>
      @if (d.plan_assessment; as a) {
        @if (a.concerns.length || a.assumptions.length || a.suggested_changes.length) {
          <section class="assessment" aria-label="Architect's plan assessment">
            <h3>Architect's assessment of the plan</h3>
            <p class="hint">Advisory: the plan is unchanged. Reject the design with feedback, or
              go back to the plan, to act on it.</p>
            @for (group of [
              { title: 'Concerns', items: a.concerns, cls: 'concerns' },
              { title: 'Assumptions', items: a.assumptions, cls: 'assumptions' },
              { title: 'Suggested plan changes', items: a.suggested_changes, cls: 'changes' },
            ]; track group.title) {
              @if (group.items.length) {
                <h4 [class]="group.cls">{{ group.title }}</h4>
                <ul>
                  @for (item of group.items; track $index) { <li>{{ item }}</li> }
                </ul>
              }
            }
          </section>
        }
      }
      <h3>Modules and contracts</h3>
      <div class="modules">
        @for (m of d.modules; track m.name) {
          <div class="module">
            <b>{{ m.name }}</b> <code>{{ m.path }}</code>
            <div>{{ m.responsibility }}</div>
            <pre>{{ m.interface }}</pre>
          </div>
        }
      </div>
      @if (d.key_decisions.length) {
        <h3>Key decisions</h3>
        <ul>
          @for (k of d.key_decisions; track $index) {
            <li>{{ k }}</li>
          }
        </ul>
      }
      <h3>Design document</h3>
      <article class="markdown" [innerHTML]="d.design_doc | markdown"></article>
    } @else {
      <p>The Architect has not produced a design yet.</p>
    }
  `,
  styles: `
    .modules { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 8px; }
    .module { border: 1px solid var(--dc-border); border-radius: 8px; padding: 8px; background: rgba(8, 17, 34, 0.6); }
    .module code { margin-left: 8px; }
    pre { white-space: pre-wrap; background: var(--dc-code-bg); padding: 6px; border-radius: 6px; font-size: 12px; }
    .markdown { border-top: 1px solid var(--dc-border); padding-top: 8px; }
    .assessment { border: 1px solid rgba(163, 132, 255, 0.45); border-radius: 10px; padding: 4px 14px 8px;
                  background: rgba(163, 132, 255, 0.07); box-shadow: 0 0 18px rgba(163, 132, 255, 0.12); }
    .assessment h3 { color: var(--dc-violet); }
    .hint { color: var(--dc-text-dim); font-size: 12px; margin-top: -6px; }
    h4 { margin: 8px 0 2px; font-size: 13px; }
    .concerns { color: var(--dc-amber); } .assumptions { color: var(--dc-cyan); } .changes { color: var(--dc-teal); }
  `,
})
export class DesignViewComponent {
  readonly design = input.required<Design | null>();
}
