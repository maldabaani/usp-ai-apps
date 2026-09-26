import { TestBed } from '@angular/core/testing';
import { provideNoopAnimations } from '@angular/platform-browser/animations';
import { Router, provideRouter } from '@angular/router';
import { of, throwError } from 'rxjs';

import { ClientConfig, RunSummary } from '../../core/api.models';
import { ApiService } from '../../core/api.service';
import { NewRunComponent } from './new-run.component';

const CONFIG: ClientConfig = { max_request_chars: 60, max_dev_iterations: 3, max_parallel_devs: 2 };

describe('NewRunComponent', () => {
  let api: jasmine.SpyObj<ApiService>;

  function setup(config = of(CONFIG)) {
    api = jasmine.createSpyObj<ApiService>('ApiService', ['createRun', 'config']);
    api.createRun.and.returnValue(of({ id: 'r9' } as RunSummary));
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
    form.setValue({ request: 'short', repo_target: 'no slash', create_repo: false });
    expect(form.valid).toBeFalse();
    form.setValue({ request: 'Build a FastAPI TODO API', repo_target: 'octocat/todo-api', create_repo: true });
    expect(form.valid).toBeTrue();
    fixture.componentInstance.submit();
    expect(api.createRun).toHaveBeenCalledWith({
      request: 'Build a FastAPI TODO API', repo_target: 'octocat/todo-api', create_repo: true,
    });
    expect(router.navigate).toHaveBeenCalledWith(['/runs', 'r9']);
  });

  it('enforces the server limit and shows the character count', () => {
    const { fixture } = setup();
    const c = fixture.componentInstance;
    c.form.setValue({ request: 'x'.repeat(61), repo_target: 'octocat/todo-api', create_repo: false });
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
});
