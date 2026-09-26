import { DecimalPipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { NonNullableFormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
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
/** Used until GET /config answers (same as the backend default). */
export const DEFAULT_MAX_REQUEST_CHARS = 20_000;

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
              <span class="count" [class.over]="tooLong()">{{ length() | number }} / {{ maxChars() | number }} characters</span>
            </div>
            @if (loaded().length) {
              <p class="loaded">Loaded: @for (f of loaded(); track f) { <code>{{ f }}</code> }</p>
            }
          </div>
          @if (tooLong()) {
            <p class="error" role="alert">Too long for the Planner's context window
              ({{ maxChars() | number }} characters max). Shorten the document or raise MAX_REQUEST_CHARS
              together with num_ctx.</p>
          }
          @if (fileError(); as e) { <p class="error" role="alert">{{ e }}</p> }

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
          @if (error(); as e) {
            <p class="error" role="alert">{{ e }}</p>
          }
          <div class="actions">
            <button mat-flat-button type="submit" class="start" [disabled]="form.invalid || tooLong() || submitting()">
              {{ submitting() ? 'Starting…' : 'Start run' }}
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
  });
  private readonly targetValue = toSignal(this.form.controls.target.valueChanges, {
    initialValue: this.form.controls.target.value,
  });
  readonly existing = computed(() => this.targetValue() === 'existing');

  private readonly config = toSignal(this.api.config().pipe(catchError(() => of(null))), {
    initialValue: null,
  });
  readonly maxChars = computed(() => this.config()?.max_request_chars ?? DEFAULT_MAX_REQUEST_CHARS);
  readonly requestText = toSignal(this.form.controls.request.valueChanges, { initialValue: '' });
  readonly length = computed(() => this.requestText().trim().length);
  readonly tooLong = computed(() => this.length() > this.maxChars());

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
    if (this.form.invalid || this.tooLong()) {
      return;
    }
    this.submitting.set(true);
    this.error.set(null);
    const value = this.form.getRawValue();
    const existing = value.target === 'existing';
    this.api.createRun({
      ...value,
      create_repo: existing ? false : value.create_repo,
      mode: existing ? value.mode : 'full',
    }).subscribe({
      next: (run) => void this.router.navigate(['/runs', run.id]),
      error: (err: { error?: { detail?: unknown } }) => {
        this.submitting.set(false);
        const detail = err.error?.detail;
        this.error.set(`Could not start the run: ${typeof detail === 'string' ? detail : JSON.stringify(detail ?? err)}`);
      },
    });
  }
}
