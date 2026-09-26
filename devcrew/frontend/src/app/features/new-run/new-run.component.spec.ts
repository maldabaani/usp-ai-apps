import { TestBed } from '@angular/core/testing';
import { provideNoopAnimations } from '@angular/platform-browser/animations';
import { Router, provideRouter } from '@angular/router';
import { of, throwError } from 'rxjs';

import { ClientConfig, RunDetail, RunSummary } from '../../core/api.models';
import { ApiService } from '../../core/api.service';
import { NewRunComponent, parseIssueRef } from './new-run.component';

const CONFIG: ClientConfig = {
  max_request_chars: 60, max_dev_iterations: 3, max_parallel_devs: 2, github_enabled: true, max_pr_rounds: 3,
  run_token_budget: 0, run_time_budget_min: 30,
};

describe('NewRunComponent', () => {
  let api: jasmine.SpyObj<ApiService>;

  function setup(config = of(CONFIG)) {
    api = jasmine.createSpyObj<ApiService>('ApiService', ['createRun', 'config', 'importIssue', 'getRun']);
    api.createRun.and.returnValue(of({ id: 'r9' } as RunSummary));
    api.importIssue.and.returnValue(of({ id: 'r10' } as RunSummary));
    api.config.and.returnValue(config);
    TestBed.configureTestingModule({
      imports: [NewRunComponent],
      providers: [provideNoopAnimations(), provideRouter([]), { provide: ApiService, useValue: api }],
    });
    const router = TestBed.inject(Router);
    spyOn(router, 'navigate').and.resolveTo(true);
    const fixture = TestBed.createComponent(NewRunComponent);
    fixture.detectChanges();
    return { fixture, router };
  }

  it('validates the form and starts a run', () => {
    const { fixture, router } = setup();
    const form = fixture.componentInstance.form;
    form.patchValue({ request: 'short', repo_target: 'no slash', create_repo: false });
    expect(form.valid).toBeFalse();
    form.patchValue({ request: 'Build a FastAPI TODO API', repo_target: 'octocat/todo-api', create_repo: true });
    expect(form.valid).toBeTrue();
    fixture.componentInstance.submit();
    expect(api.createRun).toHaveBeenCalledWith({
      request: 'Build a FastAPI TODO API', repo_target: 'octocat/todo-api', create_repo: true,
      target: 'new', mode: 'full',
    });
    expect(router.navigate).toHaveBeenCalledWith(['/runs', 'r9']);
  });

  it('works on an existing repository with a change mode, never creating the repo', () => {
    const { fixture } = setup();
    const c = fixture.componentInstance;
    const el = fixture.nativeElement as HTMLElement;
    expect(el.textContent).toContain('Create the repository if it is missing');
    c.form.patchValue({
      request: 'Add a discount field to orders', repo_target: 'acme/shop', create_repo: true,
      target: 'existing', mode: 'quick',
    });
    fixture.detectChanges();
    expect(c.existing()).toBeTrue();
    expect(el.textContent).not.toContain('Create the repository if it is missing');
    expect(el.textContent).toContain('Quick fix: you approve one change plan');
    c.submit();
    expect(api.createRun).toHaveBeenCalledWith({
      request: 'Add a discount field to orders', repo_target: 'acme/shop', create_repo: false,
      target: 'existing', mode: 'quick',
    });
  });

  it('imports a GitHub issue instead of a description', () => {
    const { fixture, router } = setup();
    const c = fixture.componentInstance;
    c.form.patchValue({ target: 'existing', source: 'issue', mode: 'quick' });
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;
    expect(c.fromIssue()).toBeTrue();
    expect(el.querySelector('textarea')).toBeNull();
    expect(el.querySelector('button.start')?.textContent).toContain('Import issue');
    c.form.controls.issue.setValue('not an issue');
    expect(c.form.valid).toBeFalse();
    c.form.controls.issue.setValue('https://github.com/acme/shop/issues/42');
    c.onIssueBlur();
    expect(c.form.controls.repo_target.value).toBe('acme/shop');
    expect(c.form.controls.issue.value).toBe('#42');
    expect(c.form.valid).toBeTrue();
    c.submit();
    expect(api.importIssue).toHaveBeenCalledWith({ repo: 'acme/shop', number: 42, mode: 'quick' });
    expect(api.createRun).not.toHaveBeenCalled();
    expect(router.navigate).toHaveBeenCalledWith(['/runs', 'r10']);
  });

  it('parses issue references', () => {
    expect(parseIssueRef('#7')).toEqual({ repo: null, number: 7 });
    expect(parseIssueRef(' 12 ')).toEqual({ repo: null, number: 12 });
    expect(parseIssueRef('https://github.com/a/b/issues/3')).toEqual({ repo: 'a/b', number: 3 });
    expect(parseIssueRef('#0')).toBeNull();
    expect(parseIssueRef('https://github.com/a/b/pull/3')).toBeNull();
  });

  it('enforces the server limit and shows the character count', () => {
    const { fixture } = setup();
    const c = fixture.componentInstance;
    c.form.patchValue({ request: 'x'.repeat(61), repo_target: 'octocat/todo-api', create_repo: false });
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;
    expect(c.tooLong()).toBeTrue();
    expect(el.querySelector('.count')?.textContent).toContain('61 / 60');
    expect(el.textContent).toContain("Too long for the Planner's context window");
    expect((el.querySelector('button.start') as HTMLButtonElement).disabled).toBeTrue();
    c.submit();
    expect(api.createRun).not.toHaveBeenCalled();
  });

  it('falls back to the default limit when /config is unreachable', () => {
    const { fixture } = setup(throwError(() => new Error('down')));
    expect(fixture.componentInstance.maxChars()).toBe(20_000);
  });

  it('loads dropped requirement files, combines them and rejects other types', async () => {
    const { fixture } = setup(of({ ...CONFIG, max_request_chars: 20_000 }));
    const c = fixture.componentInstance;
    await c.load([
      new File(['# Stories\r\n- CRUD'], 'stories.md'),
      new File(['Use pytest'], 'notes.txt'),
      new File(['%PDF'], 'spec.pdf'),
    ]);
    fixture.detectChanges();
    expect(c.form.controls.request.value).toBe('# stories.md\n\n# Stories\n- CRUD\n\n# notes.txt\n\nUse pytest');
    expect(c.loaded()).toEqual(['stories.md', 'notes.txt']);
    expect(c.fileError()).toContain('spec.pdf');
    expect(c.mode()).toBe('preview');
    const preview = (fixture.nativeElement as HTMLElement).querySelector('.preview');
    expect(preview?.querySelector('h1')?.textContent).toBe('stories.md');
  });

  it('sends an optional budget', () => {
    const { fixture } = setup();
    const c = fixture.componentInstance;
    expect(c.defaultMinutes()).toBe('default 30');
    c.form.patchValue({ request: 'Build a FastAPI TODO API', repo_target: 'octocat/todo-api', token_budget: 50000 });
    c.submit();
    expect(api.createRun).toHaveBeenCalledWith(jasmine.objectContaining({ token_budget: 50000 }));
    expect(api.createRun.calls.mostRecent().args[0].time_budget_min).toBeUndefined();
  });

  it('copies a previous run for "Run again"', () => {
    const { fixture } = setup();
    api.getRun.and.returnValue(of({
      id: 'old123456789abc', request: '# Shop\n\nAdd discounts', repo_target: 'acme/shop',
      target: 'existing', mode: 'quick',
    } as unknown as RunDetail));
    fixture.componentRef.setInput('from', 'old123456789abc');
    fixture.detectChanges();
    const c = fixture.componentInstance;
    expect(api.getRun).toHaveBeenCalledWith('old123456789abc');
    expect(c.form.getRawValue()).toEqual(jasmine.objectContaining({
      request: '# Shop\n\nAdd discounts', repo_target: 'acme/shop', target: 'existing', mode: 'quick',
    }));
    expect((fixture.nativeElement as HTMLElement).textContent).toContain('Copied from run old123456789');
  });
});
