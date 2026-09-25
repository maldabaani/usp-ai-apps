import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { NonNullableFormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatCheckboxModule } from '@angular/material/checkbox';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { Router } from '@angular/router';

import { ApiService } from '../../core/api.service';

export const REPO_PATTERN = /^[A-Za-z0-9][A-Za-z0-9-]{0,38}\/[A-Za-z0-9._-]{1,100}$/;

@Component({
  selector: 'app-new-run',
  imports: [ReactiveFormsModule, MatCardModule, MatFormFieldModule, MatInputModule, MatCheckboxModule, MatButtonModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <h1>New run</h1>
    <mat-card>
      <mat-card-content>
        <form [formGroup]="form" (ngSubmit)="submit()" class="form">
          <mat-form-field appearance="outline">
            <mat-label>Feature request</mat-label>
            <textarea matInput formControlName="request" rows="8"
              placeholder="Build a FastAPI TODO API with CRUD and pytest tests"></textarea>
            @if (form.controls.request.hasError('minlength')) {
              <mat-error>Describe the feature in at least 10 characters.</mat-error>
            }
          </mat-form-field>
          <mat-form-field appearance="outline">
            <mat-label>GitHub repository (owner/repo)</mat-label>
            <input matInput formControlName="repo_target" placeholder="octocat/todo-api" />
            @if (form.controls.repo_target.hasError('pattern')) {
              <mat-error>Use the form owner/repo.</mat-error>
            }
          </mat-form-field>
          <mat-checkbox formControlName="create_repo">Create the repository if it is missing (private)</mat-checkbox>
          @if (error(); as e) {
            <p class="error" role="alert">{{ e }}</p>
          }
          <div class="actions">
            <button mat-flat-button type="submit" [disabled]="form.invalid || submitting()">
              {{ submitting() ? 'Starting…' : 'Start run' }}
            </button>
          </div>
        </form>
      </mat-card-content>
    </mat-card>
  `,
  styles: `
    .form { display: flex; flex-direction: column; gap: 8px; max-width: 800px; }
    .actions { margin-top: 8px; }
    .error { color: #b3261e; }
  `,
})
export class NewRunComponent {
  private readonly api = inject(ApiService);
  private readonly router = inject(Router);
  readonly submitting = signal(false);
  readonly error = signal<string | null>(null);

  readonly form = inject(NonNullableFormBuilder).group({
    request: ['', [Validators.required, Validators.minLength(10)]],
    repo_target: ['', [Validators.required, Validators.pattern(REPO_PATTERN)]],
    create_repo: [false],
  });

  submit(): void {
    if (this.form.invalid) {
      return;
    }
    this.submitting.set(true);
    this.error.set(null);
    this.api.createRun(this.form.getRawValue()).subscribe({
      next: (run) => void this.router.navigate(['/runs', run.id]),
      error: (err: { error?: { detail?: unknown } }) => {
        this.submitting.set(false);
        this.error.set(`Could not start the run: ${JSON.stringify(err.error?.detail ?? err)}`);
      },
    });
  }
}
