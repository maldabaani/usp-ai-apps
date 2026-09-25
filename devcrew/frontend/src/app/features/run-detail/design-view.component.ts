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
    .module { border: 1px solid #e0e0e0; border-radius: 8px; padding: 8px; }
    .module code { margin-left: 8px; }
    pre { white-space: pre-wrap; background: #f6f8fa; padding: 6px; border-radius: 6px; font-size: 12px; }
    .markdown { border-top: 1px solid #eee; padding-top: 8px; }
  `,
})
export class DesignViewComponent {
  readonly design = input.required<Design | null>();
}
