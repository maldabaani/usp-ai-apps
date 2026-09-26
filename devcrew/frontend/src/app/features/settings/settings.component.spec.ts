import { TestBed } from '@angular/core/testing';
import { provideNoopAnimations } from '@angular/platform-browser/animations';
import { provideRouter } from '@angular/router';
import { of, throwError } from 'rxjs';

import { ClientConfig, IssueRun, WatchedRepo } from '../../core/api.models';
import { ApiService } from '../../core/api.service';
import { SettingsComponent, parseReviewers } from './settings.component';

const REPO: WatchedRepo = {
  id: 1, repo: 'acme/shop', enabled: true, poll_interval_s: 300, extra_reviewers: ['carol'],
  last_polled_at: '2026-09-01T10:00:00Z', last_error: null,
};
const ISSUE_RUN: IssueRun = {
  id: 5, repo: 'acme/shop', issue_number: 42, trigger: 'label:77', run_id: 'abcdef0123456789',
  run_status: 'awaiting_plan_approval', comment_id: 9, created_at: '2026-09-01T10:01:00Z',
};
const CONFIG: ClientConfig = {
  max_request_chars: 20000, max_dev_iterations: 3, max_parallel_devs: 2, github_enabled: false, max_pr_rounds: 3,
  run_token_budget: 0, run_time_budget_min: 0,
};

describe('SettingsComponent', () => {
  let api: jasmine.SpyObj<ApiService>;

  function setup() {
    api = jasmine.createSpyObj<ApiService>('ApiService', [
      'config', 'watchedRepos', 'issueRuns', 'addWatchedRepo', 'updateWatchedRepo', 'deleteWatchedRepo',
    ]);
    api.config.and.returnValue(of(CONFIG));
    api.watchedRepos.and.returnValue(of([REPO]));
    api.issueRuns.and.returnValue(of([ISSUE_RUN]));
    TestBed.configureTestingModule({
      imports: [SettingsComponent],
      providers: [provideNoopAnimations(), provideRouter([]), { provide: ApiService, useValue: api }],
    });
    const fixture = TestBed.createComponent(SettingsComponent);
    fixture.detectChanges();
    return fixture;
  }

  it('lists watched repositories and issue runs, and warns when GitHub is off', () => {
    const fixture = setup();
    const el = fixture.nativeElement as HTMLElement;
    expect(el.textContent).toContain('GitHub access is off');
    expect(el.querySelector('.repo-row a')?.textContent).toContain('acme/shop');
    expect(el.querySelector('.issue-list')?.textContent).toContain('acme/shop#42');
    expect(el.querySelector('.issue-list')?.textContent).toContain('label');
    fixture.destroy();
  });

  it('adds, updates and removes repositories', () => {
    const fixture = setup();
    const c = fixture.componentInstance;
    api.addWatchedRepo.and.returnValue(of({ ...REPO, id: 2, repo: 'acme/api', extra_reviewers: [] }));
    c.form.setValue({ repo: 'acme/api', interval: 120, reviewers: '@dave, erin' });
    c.add();
    expect(api.addWatchedRepo).toHaveBeenCalledWith({
      repo: 'acme/api', poll_interval_s: 120, extra_reviewers: ['dave', 'erin'],
    });
    expect(c.repos().map((r) => r.repo)).toEqual(['acme/api', 'acme/shop']);

    api.updateWatchedRepo.and.returnValue(of({ ...REPO, enabled: false }));
    c.update(REPO, { enabled: false });
    expect(c.repos().find((r) => r.id === 1)?.enabled).toBeFalse();
    c.setInterval(REPO, '10');
    expect(c.error()).toContain('at least 60');
    c.setReviewers(REPO, 'not valid!');
    expect(c.error()).toContain('GitHub user names');

    api.deleteWatchedRepo.and.returnValue(of(undefined));
    c.remove(REPO);
    expect(c.repos().map((r) => r.repo)).toEqual(['acme/api']);
    fixture.destroy();
  });

  it('shows API errors', () => {
    const fixture = setup();
    api.addWatchedRepo.and.returnValue(throwError(() => ({ error: { detail: 'acme/shop is already watched' } })));
    fixture.componentInstance.form.setValue({ repo: 'acme/shop', interval: null, reviewers: '' });
    fixture.componentInstance.add();
    expect(fixture.componentInstance.error()).toContain('already watched');
    fixture.destroy();
  });

  it('parses reviewer lists', () => {
    expect(parseReviewers('@a, b  c,b')).toEqual(['a', 'b', 'c']);
    expect(parseReviewers('')).toEqual([]);
    expect(parseReviewers('bad name!')).toBeNull();
  });
});
