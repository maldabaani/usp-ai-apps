import { DecimalPipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, computed, effect, inject, input, signal, untracked } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import {
  AbstractControl,
  NonNullableFormBuilder,
  ReactiveFormsModule,
  ValidationErrors,
  Validators,
} from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatButtonToggleModule } from '@angular/material/button-toggle';
import { MatCardModule } from '@angular/material/card';
import { MatCheckboxModule } from '@angular/material/checkbox';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { Router } from '@angular/router';
import { catchError, of } from 'rxjs';

import { RunMode, RunTarget } from '../../core/api.models';
import { ApiService } from '../../core/api.service';
import {
  ACCEPTED_EXTENSIONS,
  RequirementFile,
  combineRequirements,
  isAcceptedFile,
} from '../../core/requirements';
import { AgentIconComponent } from '../../shared/agent-icon.component';
import { MarkdownPipe } from '../../shared/markdown.pipe';

export const REPO_PATTERN = /^[A-Za-z0-9][A-Za-z0-9-]{0,38}\/[A-Za-z0-9._-]{1,100}$/;
const ISSUE_URL = /^https:\/\/github\.com\/([^/\s]+\/[^/\s]+)\/issues\/(\d+)\/?$/;

/** "#12", "12" or an issue URL (which also names the repository). */
export function parseIssueRef(text: string): { repo: string | null; number: number } | null {
  const value = text.trim();
  const url = ISSUE_URL.exec(value);
  if (url) {
    return { repo: url[1], number: Number(url[2]) };
  }
  const plain = /^#?(\d+)$/.exec(value);
  return plain && Number(plain[1]) > 0 ? { repo: null, number: Number(plain[1]) } : null;
}

/** Used until GET /config answers (same as the backend default). */
export const DEFAULT_MAX_REQUEST_CHARS = 20_000;
export const DEFAULT_MAX_DOCUMENT_CHARS = 200_000;

function issueValidator(control: AbstractControl<string>): ValidationErrors | null {
  return !control.value || parseIssueRef(control.value) ? null : { issue: true };
}

@Component({
  selector: 'app-new-run',
  imports: [
    ReactiveFormsModule, MatCardModule, MatFormFieldModule, MatInputModule, MatCheckboxModule,
    MatButtonModule, MatButtonToggleModule, MarkdownPipe, AgentIconComponent, DecimalPipe,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <header class="hero">
      <div class="crew" aria-hidden="true">
        @for (r of crew; track r) { <app-agent-icon [kind]="r" [size]="42" [active]="true" /> }
      </div>
      <div>
        <h1>New run</h1>
        <p>Describe the feature or drop a requirements document. The crew plans it, you approve
          the plan and the design, then they build, review and test it.</p>
      </div>
    </header>
    <mat-card>
      <mat-card-content>
        <form [formGroup]="form" (ngSubmit)="submit()" class="form">
          <div class="target">
            <mat-button-toggle-group formControlName="target" aria-label="What to work on" hideSingleSelectionIndicator>
              <mat-button-toggle value="new">New project</mat-button-toggle>
              <mat-button-toggle value="existing">Existing repository</mat-button-toggle>
            </mat-button-toggle-group>
            @if (existing()) {
              <mat-button-toggle-group formControlName="source" aria-label="Requirements source" hideSingleSelectionIndicator>
                <mat-button-toggle value="text">Describe</mat-button-toggle>
                <mat-button-toggle value="issue">GitHub issue</mat-button-toggle>
              </mat-button-toggle-group>
              <mat-button-toggle-group formControlName="mode" aria-label="Change flow" hideSingleSelectionIndicator>
                <mat-button-toggle value="full" title="Plan, Architect design, then development">Full</mat-button-toggle>
                <mat-button-toggle value="quick" title="One change-plan approval, no design step">Quick fix</mat-button-toggle>
              </mat-button-toggle-group>
            }
          </div>
          <p class="target-hint">
            @if (existing()) {
              DevCrew clones the repository (Python, Java/Maven or Angular), plans the change
              against the existing code and opens a PR against its default branch.
              {{ form.controls.mode.value === 'quick'
                ? 'Quick fix: you approve one change plan, then it is implemented.'
                : 'Full: you approve the plan and the Architect’s design.' }}
            } @else {
              DevCrew builds a new project from a starter template and opens a PR.
            }
          </p>
          @if (fromIssue()) {
            <mat-form-field appearance="outline">
              <mat-label>Issue (#number or URL)</mat-label>
              <input matInput formControlName="issue" placeholder="#42 or https://github.com/owner/repo/issues/42"
                     (blur)="onIssueBlur()" />
              <mat-hint>The issue title and body become the requirements; DevCrew keeps one status
                comment on the issue and the PR says “Fixes #N”.</mat-hint>
              @if (form.controls.issue.hasError('issue')) {
                <mat-error>Use #number or https://github.com/owner/repo/issues/number.</mat-error>
              }
            </mat-form-field>
          } @else {
          <div class="req-head">
            <span class="req-label">Requirements</span>
            <mat-button-toggle-group [value]="mode()" (change)="mode.set($event.value)" aria-label="Editor mode" hideSingleSelectionIndicator>
              <mat-button-toggle value="edit">Edit</mat-button-toggle>
              <mat-button-toggle value="preview">Preview</mat-button-toggle>
            </mat-button-toggle-group>
          </div>

          <div class="drop" [class.over]="dragOver()" (dragover)="onDragOver($event)"
               (dragleave)="dragOver.set(false)" (drop)="onDrop($event)">
            @if (mode() === 'edit') {
              <mat-form-field appearance="outline" class="editor">
                <textarea matInput formControlName="request" rows="14" aria-label="Requirements"
                  placeholder="Build a FastAPI TODO API with CRUD and pytest tests&#10;&#10;…or paste / drop a Markdown requirements document"></textarea>
                @if (form.controls.request.hasError('minlength')) {
                  <mat-error>Describe the feature in at least 10 characters.</mat-error>
                }
              </mat-form-field>
            } @else {
              <article class="preview markdown" [innerHTML]="(requestText() || '_Nothing to preview yet._') | markdown"></article>
            }
            <div class="files">
              <button mat-stroked-button type="button" (click)="picker.click()">Upload .md / .txt</button>
              <input #picker type="file" hidden multiple [accept]="accept" (change)="onPick($event)" />
              <span class="hint">or drag files here · several files are combined under their names</span>
              <span class="count" [class.over]="tooLong()">{{ length() | number }} / {{ (condensed() ? maxDocument() : maxChars()) | number }} characters</span>
            </div>
            @if (loaded().length) {
              <p class="loaded">Loaded: @for (f of loaded(); track f) { <code>{{ f }}</code> }</p>
            }
          </div>
          @if (tooLong()) {
            <p class="error" role="alert">Too long: documents can have at most
              {{ maxDocument() | number }} characters (MAX_DOCUMENT_CHARS). Shorten or split it.</p>
          } @else if (condensed()) {
            <p class="note" role="status">Long document: over {{ maxChars() | number }} characters it is
              condensed for the Planner and Architect, part by part. The Planner can still search the
              full text, and the run keeps it.</p>
          }
          @if (fileError(); as e) { <p class="error" role="alert">{{ e }}</p> }
          }

          <mat-form-field appearance="outline">
            <mat-label>GitHub repository (owner/repo)</mat-label>
            <input matInput formControlName="repo_target" placeholder="octocat/todo-api" />
            @if (form.controls.repo_target.hasError('pattern')) {
              <mat-error>Use the form owner/repo.</mat-error>
            }
          </mat-form-field>
          @if (!existing()) {
            <mat-checkbox formControlName="create_repo">Create the repository if it is missing (private)</mat-checkbox>
          }
          @if (!fromIssue()) {
            <details class="budget" [open]="budgetOpen()">
              <summary>Budget (optional)</summary>
              <div class="budget-fields">
                <mat-form-field appearance="outline">
                  <mat-label>Tokens</mat-label>
                  <input matInput type="number" min="0" formControlName="token_budget" [placeholder]="defaultTokens()" />
                </mat-form-field>
                <mat-form-field appearance="outline">
                  <mat-label>Working minutes</mat-label>
                  <input matInput type="number" min="0" formControlName="time_budget_min" [placeholder]="defaultMinutes()" />
                </mat-form-field>
              </div>
              <p class="target-hint">Checked before every wave of tasks: at the limit the run asks you to
                continue or stop. Empty uses the server default; 0 means no limit.</p>
            </details>
          }
          @if (fromRun()) { <p class="target-hint">Copied from run <code>{{ fromRun()!.slice(0, 12) }}</code>; edit anything before starting.</p> }
          @if (error(); as e) {
            <p class="error" role="alert">{{ e }}</p>
          }
          <div class="actions">
            <button mat-flat-button type="submit" class="start"
                    [disabled]="form.invalid || (!fromIssue() && tooLong()) || submitting()">
              {{ submitting() ? 'Starting…' : fromIssue() ? 'Import issue' : 'Start run' }}
            </button>
          </div>
        </form>
      </mat-card-content>
    </mat-card>
  `,
  styles: `
    .hero { display: flex; gap: 18px; align-items: center; margin: 8px 0 16px; }
    .hero p { color: var(--dc-text-dim); margin: 4px 0 0; max-width: 720px; }
    .hero h1 { margin: 0; }
    .crew { display: flex; gap: 6px; padding: 8px 12px; border-radius: 14px; border: 1px solid var(--dc-border);
            background: rgba(8, 17, 34, 0.7); box-shadow: 0 0 24px rgba(62, 230, 255, 0.12); }
    .form { display: flex; flex-direction: column; gap: 8px; max-width: 980px; }
    .target { display: flex; gap: 12px; flex-wrap: wrap; align-items: center; }
    .target-hint { color: var(--dc-text-dim); font-size: 13px; margin: 2px 0 8px; }
    .req-head { display: flex; justify-content: space-between; align-items: center; }
    .req-label { font-weight: 600; color: var(--dc-cyan); letter-spacing: 0.4px; }
    .drop { border: 1px dashed var(--dc-border-strong); border-radius: 12px; padding: 12px; transition: all 0.15s; }
    .drop.over { border-color: var(--dc-cyan); box-shadow: var(--dc-glow-cyan); background: rgba(62, 230, 255, 0.05); }
    .editor { width: 100%; }
    textarea { font-family: var(--dc-mono); font-size: 13px; }
    .preview { min-height: 260px; max-height: 480px; overflow: auto; padding: 4px 12px; }
    .files { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
    .hint { color: var(--dc-text-faint); font-size: 12px; }
    .count { margin-left: auto; font-size: 12px; color: var(--dc-text-dim); font-family: var(--dc-mono); }
    .count.over { color: var(--dc-red); }
    .loaded { font-size: 12px; color: var(--dc-text-dim); margin: 6px 0 0; }
    .loaded code { margin-right: 6px; color: var(--dc-teal); }
    .actions { margin-top: 8px; }
    .start { box-shadow: var(--dc-glow-cyan); }
    .error { color: var(--dc-red); }
    .note { color: var(--dc-amber); font-size: 13px; }
    .budget summary { cursor: pointer; color: var(--dc-text-dim); font-size: 13px; }
    .budget-fields { display: flex; gap: 12px; margin-top: 8px; }
    .budget-fields mat-form-field { width: 180px; }
  `,
})
export class NewRunComponent {
  private readonly api = inject(ApiService);
  private readonly router = inject(Router);
  readonly submitting = signal(false);
  readonly error = signal<string | null>(null);
  readonly fileError = signal<string | null>(null);
  readonly mode = signal<'edit' | 'preview'>('edit');
  readonly dragOver = signal(false);
  readonly loaded = signal<string[]>([]);
  readonly accept = ACCEPTED_EXTENSIONS.join(',');
  readonly crew = ['planner', 'architect', 'developer', 'reviewer', 'qa'];

  readonly form = inject(NonNullableFormBuilder).group({
    request: ['', [Validators.required, Validators.minLength(10)]],
    repo_target: ['', [Validators.required, Validators.pattern(REPO_PATTERN)]],
    create_repo: [false],
    target: ['new' as RunTarget],
    mode: ['full' as RunMode],
    source: ['text' as 'text' | 'issue'],
    token_budget: [null as number | null, [Validators.min(0)]],
    time_budget_min: [null as number | null, [Validators.min(0)]],
    issue: [{ value: '', disabled: true }, [Validators.required, issueValidator]],
  });
  private readonly targetValue = toSignal(this.form.controls.target.valueChanges, {
    initialValue: this.form.controls.target.value,
  });
  readonly existing = computed(() => this.targetValue() === 'existing');
  private readonly sourceValue = toSignal(this.form.controls.source.valueChanges, {
    initialValue: this.form.controls.source.value,
  });
  readonly fromIssue = computed(() => this.existing() && this.sourceValue() === 'issue');

  /** ?from=<run id>: "Run again" prefills the form from that run. */
  readonly from = input<string | undefined>(undefined);
  readonly fromRun = signal<string | null>(null);
  readonly budgetOpen = signal(false);
  readonly defaultTokens = computed(() => this.budgetHint(this.config()?.run_token_budget));
  readonly defaultMinutes = computed(() => this.budgetHint(this.config()?.run_time_budget_min));

  private budgetHint(value: number | undefined): string {
    return value ? `default ${value}` : 'default: none';
  }

  constructor() {
    effect(() => {
      const id = this.from();
      if (id) {
        untracked(() => this.prefill(id));
      }
    });
    // only the active requirements source is validated
    effect(() => {
      const issue = this.fromIssue();
      const { request, issue: issueControl } = this.form.controls;
      if (issue) {
        request.disable({ emitEvent: false });
        issueControl.enable({ emitEvent: false });
      } else {
        request.enable({ emitEvent: false });
        issueControl.disable({ emitEvent: false });
      }
    });
  }

  private prefill(id: string): void {
    this.api.getRun(id).subscribe({
      next: (run) => {
        this.form.patchValue({
          request: run.request,
          repo_target: run.repo_target,
          target: run.target ?? 'new',
          mode: run.mode ?? 'full',
          create_repo: false,
        });
        this.fromRun.set(run.id);
        this.mode.set('preview');
      },
      error: () => this.error.set(`Could not load run ${id} to copy it.`),
    });
  }

  /** An issue URL also fills in the repository. */
  onIssueBlur(): void {
    const ref = parseIssueRef(this.form.controls.issue.value);
    if (ref?.repo) {
      this.form.controls.repo_target.setValue(ref.repo);
      this.form.controls.issue.setValue(`#${ref.number}`);
    }
  }

  private readonly config = toSignal(this.api.config().pipe(catchError(() => of(null))), {
    initialValue: null,
  });
  readonly maxChars = computed(() => this.config()?.max_request_chars ?? DEFAULT_MAX_REQUEST_CHARS);
  readonly requestText = toSignal(this.form.controls.request.valueChanges, { initialValue: '' });
  readonly length = computed(() => this.requestText().trim().length);
  readonly maxDocument = computed(() => this.config()?.max_document_chars ?? DEFAULT_MAX_DOCUMENT_CHARS);
  /** Over MAX_REQUEST_CHARS the backend condenses the document for planning (Phase 15). */
  readonly condensed = computed(() => this.length() > this.maxChars() && !this.tooLong());
  readonly tooLong = computed(() => this.length() > this.maxDocument());

  onDragOver(event: DragEvent): void {
    event.preventDefault();
    this.dragOver.set(true);
  }

  onDrop(event: DragEvent): void {
    event.preventDefault();
    this.dragOver.set(false);
    void this.load(Array.from(event.dataTransfer?.files ?? []));
  }

  onPick(event: Event): void {
    const input = event.target as HTMLInputElement;
    void this.load(Array.from(input.files ?? []));
    input.value = ''; // picking the same file again should reload it
  }

  /** Read accepted files into the editor (replacing its content). */
  async load(files: File[]): Promise<void> {
    this.fileError.set(null);
    const accepted = files.filter((f) => isAcceptedFile(f.name));
    const rejected = files.filter((f) => !isAcceptedFile(f.name)).map((f) => f.name);
    if (rejected.length) {
      this.fileError.set(`Only ${ACCEPTED_EXTENSIONS.join(', ')} files are supported: ${rejected.join(', ')}`);
    }
    if (!accepted.length) {
      return;
    }
    const docs: RequirementFile[] = await Promise.all(
      accepted.map(async (f) => ({ name: f.name, text: await f.text() })),
    );
    this.form.controls.request.setValue(combineRequirements(docs));
    this.form.controls.request.markAsDirty();
    this.loaded.set(docs.map((d) => d.name));
    this.mode.set('preview');
  }

  submit(): void {
    if (this.form.invalid || (!this.fromIssue() && this.tooLong())) {
      return;
    }
    this.submitting.set(true);
    this.error.set(null);
    const value = this.form.getRawValue();
    const existing = value.target === 'existing';
    const ref = parseIssueRef(value.issue);
    const started = this.fromIssue() && ref
      ? this.api.importIssue({
          repo: ref.repo ?? value.repo_target,
          number: ref.number,
          mode: value.mode,
        })
      : this.api.createRun({
          request: value.request,
          repo_target: value.repo_target,
          target: value.target,
          create_repo: existing ? false : value.create_repo,
          mode: existing ? value.mode : 'full',
          ...(value.token_budget !== null ? { token_budget: value.token_budget } : {}),
          ...(value.time_budget_min !== null ? { time_budget_min: value.time_budget_min } : {}),
        });
    started.subscribe({
      next: (run) => void this.router.navigate(['/runs', run.id]),
      error: (err: { error?: { detail?: unknown } }) => {
        this.submitting.set(false);
        const detail = err.error?.detail;
        this.error.set(`Could not start the run: ${typeof detail === 'string' ? detail : JSON.stringify(detail ?? err)}`);
      },
    });
  }
}
