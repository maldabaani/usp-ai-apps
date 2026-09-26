import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, DestroyRef, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed, toSignal } from '@angular/core/rxjs-interop';
import { NonNullableFormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSlideToggleModule } from '@angular/material/slide-toggle';
import { RouterLink } from '@angular/router';
import { catchError, forkJoin, interval, of, startWith, switchMap } from 'rxjs';

import { IssueRun, WatchedRepo, WatchedRepoUpdate } from '../../core/api.models';
import { ApiService } from '../../core/api.service';
import { AgentIconComponent } from '../../shared/agent-icon.component';
import { StatusChipComponent } from '../../shared/status-chip.component';
import { REPO_PATTERN } from '../new-run/new-run.component';

const REFRESH_MS = 10_000;
export const MIN_POLL_S = 60;
const USER = /^[A-Za-z0-9][A-Za-z0-9-]{0,38}$/;

/** "@alice, bob" -> ["alice", "bob"]; null when a name is not a GitHub user name. */
export function parseReviewers(text: string): string[] | null {
  const names = text
    .split(/[\s,]+/)
    .map((n) => n.trim().replace(/^@/, ''))
    .filter(Boolean);
  return names.every((n) => USER.test(n)) ? [...new Set(names)] : null;
}

interface ApiError { error?: { detail?: unknown } }

function detail(err: ApiError): string {
  const d = err.error?.detail;
  return typeof d === 'string' ? d : JSON.stringify(d ?? err);
}

/** GitHub automation: watched repositories (issue intake + PR follow-up) and issue runs. */
@Component({
  selector: 'app-settings',
  imports: [
    ReactiveFormsModule, RouterLink, DatePipe, MatCardModule, MatButtonModule, MatFormFieldModule,
    MatInputModule, MatSlideToggleModule, StatusChipComponent, AgentIconComponent,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <header class="hero">
      <app-agent-icon kind="delivery" [size]="44" [active]="true" />
      <div>
        <h1>GitHub automation</h1>
        <p>DevCrew polls these repositories. Label an issue <code>devcrew</code> for a Full run or
          <code>devcrew:quick</code> for a Quick fix; the run still stops at plan approval. After the PR
          is open, review comments from people with write access (or listed reviewers), failed checks
          and merge conflicts are handled in follow-up rounds (at most {{ maxRounds() }} automatic
          rounds; bigger changes wait for you).</p>
      </div>
    </header>
    @if (config() && !config()!.github_enabled) {
      <p class="warn" role="alert">GitHub access is off: set <code>GITHUB_TOKEN</code> and
        <code>GITHUB_DELIVERY_ENABLED=true</code> in the backend <code>.env</code>. Nothing is polled until then.</p>
    }
    @if (error(); as e) { <p class="error" role="alert">{{ e }}</p> }

    <mat-card>
      <mat-card-content>
        <h2>Watched repositories</h2>
        <form [formGroup]="form" (ngSubmit)="add()" class="add">
          <mat-form-field appearance="outline" class="repo">
            <mat-label>Repository (owner/repo)</mat-label>
            <input matInput formControlName="repo" placeholder="acme/shop" />
            @if (form.controls.repo.hasError('pattern')) { <mat-error>Use the form owner/repo.</mat-error> }
          </mat-form-field>
          <mat-form-field appearance="outline" class="interval">
            <mat-label>Poll every (s)</mat-label>
            <input matInput type="number" formControlName="interval" [min]="minPoll" placeholder="300" />
            @if (form.controls.interval.hasError('min')) { <mat-error>At least {{ minPoll }} s.</mat-error> }
          </mat-form-field>
          <mat-form-field appearance="outline" class="reviewers">
            <mat-label>Extra reviewers</mat-label>
            <input matInput formControlName="reviewers" placeholder="@alice, bob" />
            <mat-hint>Acted on even without write access</mat-hint>
          </mat-form-field>
          <button mat-flat-button type="submit" [disabled]="form.invalid || busy()">Watch</button>
        </form>

        @if (repos().length === 0) {
          <p class="empty">No repositories watched yet.</p>
        }
        <ul class="repos">
          @for (r of repos(); track r.id) {
            <li class="repo-row" [class.off]="!r.enabled">
              <div class="name">
                <a [href]="'https://github.com/' + r.repo" target="_blank" rel="noopener">{{ r.repo }}</a>
                <span class="polled">
                  @if (r.last_polled_at) { polled {{ r.last_polled_at | date: 'short' }} } @else { not polled yet }
                </span>
                @if (r.last_error) { <span class="row-error" [title]="r.last_error">⚠ {{ r.last_error }}</span> }
              </div>
              <mat-slide-toggle [checked]="r.enabled" (change)="update(r, { enabled: $event.checked })"
                                [attr.aria-label]="'Watch ' + r.repo">{{ r.enabled ? 'on' : 'off' }}</mat-slide-toggle>
              <mat-form-field appearance="outline" class="interval" subscriptSizing="dynamic">
                <mat-label>Poll (s)</mat-label>
                <input matInput type="number" [min]="minPoll" [value]="r.poll_interval_s"
                       (change)="setInterval(r, $any($event.target).value)" />
              </mat-form-field>
              <mat-form-field appearance="outline" class="reviewers" subscriptSizing="dynamic">
                <mat-label>Extra reviewers</mat-label>
                <input matInput [value]="r.extra_reviewers.join(', ')"
                       (change)="setReviewers(r, $any($event.target).value)" />
              </mat-form-field>
              <button mat-button class="remove" type="button" (click)="remove(r)">Remove</button>
            </li>
          }
        </ul>
      </mat-card-content>
    </mat-card>

    <mat-card class="issues">
      <mat-card-content>
        <h2>Issue runs</h2>
        @if (issueRuns().length === 0) {
          <p class="empty">No runs from issues yet. Label an issue, or import one on <a routerLink="/runs/new">New run</a>.</p>
        }
        <ul class="issue-list">
          @for (i of issueRuns(); track i.id) {
            <li>
              <a [href]="'https://github.com/' + i.repo + '/issues/' + i.issue_number" target="_blank" rel="noopener">
                {{ i.repo }}#{{ i.issue_number }}</a>
              <span class="trigger">{{ i.trigger.startsWith('manual:') ? 'imported' : 'label' }}</span>
              <a [routerLink]="['/runs', i.run_id]" class="run">run {{ i.run_id.slice(0, 12) }}</a>
              @if (i.run_status) { <app-status-chip [status]="i.run_status" /> }
              <span class="when">{{ i.created_at | date: 'short' }}</span>
            </li>
          }
        </ul>
      </mat-card-content>
    </mat-card>
  `,
  styles: `
    .hero { display: flex; gap: 18px; align-items: center; margin: 8px 0 16px; }
    .hero h1 { margin: 0; }
    .hero p { color: var(--dc-text-dim); margin: 4px 0 0; max-width: 900px; }
    code { color: var(--dc-teal); }
    h2 { margin: 0 0 12px; font-size: 16px; color: var(--dc-cyan); letter-spacing: 0.4px; }
    .add { display: flex; gap: 12px; align-items: baseline; flex-wrap: wrap; }
    .repo { flex: 1 1 240px; }
    .interval { width: 130px; }
    .reviewers { flex: 1 1 220px; }
    .repos, .issue-list { list-style: none; padding: 0; margin: 8px 0 0; display: flex; flex-direction: column; gap: 8px; }
    .repo-row { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; padding: 10px 12px;
                border: 1px solid var(--dc-border); border-radius: 12px; background: rgba(8, 17, 34, 0.6); }
    .repo-row.off { opacity: 0.6; }
    .name { display: flex; flex-direction: column; flex: 1 1 220px; gap: 2px; }
    .name a { font-weight: 600; color: var(--dc-text); }
    .polled { font-size: 12px; color: var(--dc-text-faint); }
    .row-error { font-size: 12px; color: var(--dc-red); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 420px; }
    .issues { margin-top: 16px; }
    .issue-list li { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
    .trigger { font-size: 12px; color: var(--dc-text-dim); }
    .run { font-family: var(--dc-mono); font-size: 12px; }
    .when { margin-left: auto; font-size: 12px; color: var(--dc-text-faint); }
    .empty { color: var(--dc-text-dim); }
    .error { color: var(--dc-red); }
    .warn { color: var(--dc-amber); border: 1px solid rgba(255, 193, 77, 0.45); border-radius: 10px; padding: 8px 12px;
            background: rgba(255, 193, 77, 0.08); }
  `,
})
export class SettingsComponent {
  private readonly api = inject(ApiService);
  private readonly destroyRef = inject(DestroyRef);
  readonly repos = signal<WatchedRepo[]>([]);
  readonly issueRuns = signal<IssueRun[]>([]);
  readonly error = signal<string | null>(null);
  readonly busy = signal(false);
  readonly minPoll = MIN_POLL_S;
  readonly config = toSignal(this.api.config().pipe(catchError(() => of(null))), { initialValue: null });
  readonly maxRounds = computed(() => this.config()?.max_pr_rounds ?? 3);

  readonly form = inject(NonNullableFormBuilder).group({
    repo: ['', [Validators.required, Validators.pattern(REPO_PATTERN)]],
    interval: [null as number | null, [Validators.min(MIN_POLL_S)]],
    reviewers: [''],
  });

  constructor() {
    interval(REFRESH_MS)
      .pipe(
        startWith(0),
        switchMap(() => forkJoin([this.api.watchedRepos(), this.api.issueRuns()]).pipe(
          catchError((err: ApiError) => {
            this.error.set(`Could not load: ${detail(err)}`);
            return of(null);
          }),
        )),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe((result) => {
        if (result) {
          this.repos.set(result[0]);
          this.issueRuns.set(result[1]);
        }
      });
  }

  add(): void {
    const value = this.form.getRawValue();
    const reviewers = parseReviewers(value.reviewers);
    if (this.form.invalid || reviewers === null) {
      this.error.set(reviewers === null ? 'Extra reviewers must be GitHub user names.' : null);
      return;
    }
    this.busy.set(true);
    this.error.set(null);
    this.api
      .addWatchedRepo({ repo: value.repo.trim(), poll_interval_s: value.interval || null, extra_reviewers: reviewers })
      .subscribe({
        next: (row) => {
          this.busy.set(false);
          this.repos.update((rows) => [...rows, row].sort((a, b) => a.repo.localeCompare(b.repo)));
          this.form.reset();
        },
        error: (err: ApiError) => {
          this.busy.set(false);
          this.error.set(`Could not watch ${value.repo}: ${detail(err)}`);
        },
      });
  }

  update(repo: WatchedRepo, change: WatchedRepoUpdate): void {
    this.error.set(null);
    this.api.updateWatchedRepo(repo.id, change).subscribe({
      next: (row) => this.repos.update((rows) => rows.map((r) => (r.id === row.id ? row : r))),
      error: (err: ApiError) => this.error.set(`Could not update ${repo.repo}: ${detail(err)}`),
    });
  }

  setInterval(repo: WatchedRepo, raw: string): void {
    const seconds = Number(raw);
    if (!Number.isInteger(seconds) || seconds < MIN_POLL_S) {
      this.error.set(`The poll interval must be at least ${MIN_POLL_S} seconds.`);
      return;
    }
    this.update(repo, { poll_interval_s: seconds });
  }

  setReviewers(repo: WatchedRepo, raw: string): void {
    const reviewers = parseReviewers(raw);
    if (reviewers === null) {
      this.error.set('Extra reviewers must be GitHub user names.');
      return;
    }
    this.update(repo, { extra_reviewers: reviewers });
  }

  remove(repo: WatchedRepo): void {
    this.api.deleteWatchedRepo(repo.id).subscribe({
      next: () => this.repos.update((rows) => rows.filter((r) => r.id !== repo.id)),
      error: (err: ApiError) => this.error.set(`Could not remove ${repo.repo}: ${detail(err)}`),
    });
  }
}
