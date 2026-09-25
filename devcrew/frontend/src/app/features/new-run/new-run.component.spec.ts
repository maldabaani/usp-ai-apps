import { TestBed } from '@angular/core/testing';
import { provideNoopAnimations } from '@angular/platform-browser/animations';
import { Router, provideRouter } from '@angular/router';
import { of } from 'rxjs';

import { RunSummary } from '../../core/api.models';
import { ApiService } from '../../core/api.service';
import { NewRunComponent } from './new-run.component';

describe('NewRunComponent', () => {
  it('validates the form and starts a run', () => {
    const api = jasmine.createSpyObj<ApiService>('ApiService', ['createRun']);
    api.createRun.and.returnValue(of({ id: 'r9' } as RunSummary));
    TestBed.configureTestingModule({
      imports: [NewRunComponent],
      providers: [provideNoopAnimations(), provideRouter([]), { provide: ApiService, useValue: api }],
    });
    const router = TestBed.inject(Router);
    spyOn(router, 'navigate').and.resolveTo(true);
    const fixture = TestBed.createComponent(NewRunComponent);
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
});
