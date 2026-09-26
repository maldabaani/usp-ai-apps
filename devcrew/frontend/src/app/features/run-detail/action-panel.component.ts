import { ChangeDetectionStrategy, Component, computed, input, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';

import { IntegrationSummary, PendingInput, ResumeAction, ResumeRequest, TaskState } from '../../core/api.models';

type Mode = 'none' | 'reject' | 'edit' | 'answer';

/**
 * What the human can do for one pending interrupt: approve / reject (with feedback) / edit (the
 * plan or design as JSON) for approvals, an answer box for questions, retry / guidance / give up
 * for escalations. Only actions the backend allows for this interrupt are shown.
 */
@Component({
  selector: 'app-action-panel',
  imports: [FormsModule, MatCardModule, MatButtonModule, MatFormFieldModule, MatInputModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <mat-card class="panel" [class]="'panel ' + pending().kind">
      <mat-card-header>
        <mat-card-title>{{ pending().title }}</mat-card-title>
        <mat-card-subtitle>{{ subtitle() }}</mat-card-subtitle>
      </mat-card-header>
      <mat-card-content>
        @if (pending().error; as err) {
          <p class="error" role="alert">{{ err }}</p>
        }
        @if (question(); as q) {
          <p class="question">{{ q }}</p>
        }
        @if (reason(); as r) {
          <pre class="reason">{{ r }}</pre>
        }
        @if (finalSummary(); as f) {
          <p>
            Merged: <b>{{ f.merged.join(', ') || 'none' }}</b>
            @if (f.failed.length) { · failed: <b class="bad">{{ f.failed.join(', ') }}</b> }
            @if (f.blocked.length) { · blocked: <b class="bad">{{ f.blocked.join(', ') }}</b> }
          </p>
          <ul>
            @for (t of testRows(); track t.stack) {
              <li>{{ t.stack }} tests: {{ t.text }}</li>
            }
          </ul>
        }

        @switch (mode()) {
          @case ('reject') {
            <mat-form-field appearance="outline" class="wide">
              <mat-label>{{ labels().rejectPrompt }}</mat-label>
              <textarea matInput rows="4" [ngModel]="text()" (ngModelChange)="text.set($event)"></textarea>
            </mat-form-field>
          }
          @case ('answer') {
            <mat-form-field appearance="outline" class="wide">
              <mat-label>{{ pending().kind === 'escalation' ? 'Guidance for the next attempt' : 'Your answer' }}</mat-label>
              <textarea matInput rows="4" [ngModel]="text()" (ngModelChange)="text.set($event)"></textarea>
            </mat-form-field>
          }
          @case ('edit') {
            <mat-form-field appearance="outline" class="wide">
              <mat-label>{{ pending().artifact }} (JSON)</mat-label>
              <textarea matInput rows="18" class="json" [ngModel]="json()" (ngModelChange)="json.set($event)"></textarea>
            </mat-form-field>
            @if (jsonError(); as e) {
              <p class="error">{{ e }}</p>
            }
          }
        }
      </mat-card-content>
      <mat-card-actions>
        @if (mode() === 'none') {
          @if (allows('approve')) {
            <button mat-flat-button (click)="send('approve')" [disabled]="busy()">
              {{ labels().approve }}
            </button>
          }
          @if (allows('answer')) {
            <button mat-stroked-button (click)="mode.set('answer')" [disabled]="busy()">
              {{ pending().kind === 'escalation' ? 'Retry with guidance' : 'Answer' }}
            </button>
          }
          @if (allows('edit')) {
            <button mat-stroked-button (click)="startEdit()" [disabled]="busy()">Edit</button>
          }
          @if (allows('reject')) {
            <button mat-stroked-button (click)="mode.set('reject')" [disabled]="busy()">
              {{ labels().reject }}
            </button>
          }
        } @else {
          <button mat-flat-button (click)="submitMode()" [disabled]="busy() || !canSubmit()">Send</button>
          <button mat-button (click)="mode.set('none')" [disabled]="busy()">Back</button>
        }
      </mat-card-actions>
    </mat-card>
  `,
  styles: `
    .panel { border-left: 4px solid var(--dc-amber); margin-bottom: 12px; }
    .panel.question { border-left-color: var(--dc-blue); }
    .panel.escalation { border-left-color: var(--dc-red); }
    .wide { width: 100%; }
    .json { font-family: ui-monospace, monospace; font-size: 12px; }
    .question { font-size: 16px; font-weight: 500; }
    .reason { white-space: pre-wrap; background: rgba(255, 90, 122, 0.08); padding: 8px; border-radius: 6px; font-size: 12px; }
    .error, .bad { color: var(--dc-red); }
  `,
})
export class ActionPanelComponent {
  readonly pending = input.required<PendingInput>();
  readonly busy = input(false);
  readonly tasks = input<Record<string, TaskState>>({});
  readonly resumeRequested = output<ResumeRequest>();

  readonly mode = signal<Mode>('none');
  readonly text = signal('');
  readonly json = signal('');
  readonly jsonError = signal<string | null>(null);

  readonly question = computed(() => {
    const q = this.pending().data['question'];
    return typeof q === 'string' && this.pending().kind === 'question' ? q : null;
  });
  readonly reason = computed(() => {
    const data = this.pending().data;
    if (this.pending().kind !== 'escalation') {
      return null;
    }
    const q = typeof data['question'] === 'string' ? data['question'] : '';
    const r = typeof data['reason'] === 'string' ? data['reason'] : '';
    return q && q !== r ? `${q}\n\n${r}` : r;
  });
  readonly finalSummary = computed(
    () => (this.pending().artifact === 'final' ? (this.pending().data['integration'] as IntegrationSummary | null) : null),
  );
  readonly testRows = computed(() =>
    Object.entries(this.finalSummary()?.tests ?? {}).map(([stack, t]) => ({
      stack,
      text: !t.ran ? 'not run' : t.passed ? 'passed' : 'FAILED',
    })),
  );
  readonly subtitle = computed(() => {
    const p = this.pending();
    const task = p.data['task_id'];
    const role = p.data['role'];
    return [p.kind, typeof role === 'string' ? `from ${role}` : '', typeof task === 'string' ? `task ${task}` : '']
      .filter(Boolean)
      .join(' · ');
  });

  /** Button wording depends on what is being decided. */
  readonly labels = computed(() => {
    const p = this.pending();
    if (p.kind !== 'escalation') {
      return { approve: 'Approve', reject: 'Reject', rejectPrompt: 'What should change?' };
    }
    if (p.data['node'] === 'github_delivery') {
      return { approve: 'Retry delivery', reject: 'Finish without PR', rejectPrompt: 'Why skip the pull request?' };
    }
    return { approve: 'Retry', reject: 'Give up on task', rejectPrompt: 'Why give up on it?' };
  });

  allows(action: ResumeAction): boolean {
    return this.pending().allowed_actions.includes(action);
  }

  canSubmit(): boolean {
    return this.mode() === 'edit' ? this.json().trim().length > 0 : this.text().trim().length > 0;
  }

  startEdit(): void {
    const artifact = this.pending().artifact;
    const value = artifact ? this.pending().data[artifact] : undefined;
    this.json.set(JSON.stringify(value ?? {}, null, 2));
    this.jsonError.set(null);
    this.mode.set('edit');
  }

  send(action: ResumeAction): void {
    this.resumeRequested.emit({ action, interrupt_id: this.pending().interrupt_id });
  }

  submitMode(): void {
    const base = { interrupt_id: this.pending().interrupt_id };
    switch (this.mode()) {
      case 'reject':
        this.resumeRequested.emit({ ...base, action: 'reject', feedback: this.text().trim() });
        break;
      case 'answer':
        this.resumeRequested.emit({ ...base, action: 'answer', answer: this.text().trim() });
        break;
      case 'edit':
        try {
          const artifact = JSON.parse(this.json()) as unknown;
          if (typeof artifact !== 'object' || artifact === null || Array.isArray(artifact)) {
            throw new Error('the artifact must be a JSON object');
          }
          this.resumeRequested.emit({ ...base, action: 'edit', artifact: artifact as Record<string, unknown> });
        } catch (e) {
          this.jsonError.set(`Invalid JSON: ${(e as Error).message}`);
          return;
        }
        break;
      default:
        return;
    }
    this.mode.set('none');
    this.text.set('');
  }
}
