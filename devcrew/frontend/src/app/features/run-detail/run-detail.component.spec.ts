import { signal } from '@angular/core';
import { ComponentFixture, TestBed, fakeAsync, tick } from '@angular/core/testing';
import { provideNoopAnimations } from '@angular/platform-browser/animations';
import { provideRouter } from '@angular/router';
import { of } from 'rxjs';

import { RunDetail, RunEvent } from '../../core/api.models';
import { ApiService } from '../../core/api.service';
import { RunEventStream, RunEventsService } from '../../core/run-events.service';
import { RunDetailComponent } from './run-detail.component';

const RUN: RunDetail = {
  id: 'r1', request: 'Build a TODO API', repo_target: 'me/todo', create_repo: false,
  status: 'awaiting_plan_approval', pr_url: null, error: null, created_at: null, updated_at: null,
  busy: false, plan: { summary: 'TODO', user_stories: [], tasks: [] }, design: null, tasks: {},
  qa_log: [], integration: null, integration_branch: null, wave: 0, errors: [],
  pending: [{ interrupt_id: 'i1', kind: 'approval', title: 'Approve the plan', artifact: 'plan',
              allowed_actions: ['approve', 'reject', 'edit'], data: {}, error: null }],
};

describe('RunDetailComponent', () => {
  let fixture: ComponentFixture<RunDetailComponent>;
  let api: jasmine.SpyObj<ApiService>;
  let onEvent: (e: RunEvent) => void;
  let stream: jasmine.SpyObj<RunEventStream>;

  beforeEach(() => {
    api = jasmine.createSpyObj<ApiService>('ApiService', ['getRun', 'resume', 'cancel', 'diff', 'listFiles']);
    api.getRun.and.returnValue(of(RUN));
    api.resume.and.returnValue(of(RUN.pending[0]));
    stream = jasmine.createSpyObj<RunEventStream>('RunEventStream', ['close'], {
      events: signal<RunEvent[]>([]), state: signal('open'),
    } as never);
    const streams = { connect: (_: string, cb: (e: RunEvent) => void) => ((onEvent = cb), stream) };
    TestBed.configureTestingModule({
      imports: [RunDetailComponent],
      providers: [
        provideNoopAnimations(), provideRouter([]),
        { provide: ApiService, useValue: api }, { provide: RunEventsService, useValue: streams },
      ],
    });
    fixture = TestBed.createComponent(RunDetailComponent);
    fixture.componentRef.setInput('id', 'r1');
    fixture.detectChanges();
  });

  it('renders the run with its pending approval and resumes', () => {
    const el = fixture.nativeElement as HTMLElement;
    expect(el.textContent).toContain('Build a TODO API');
    expect(el.textContent).toContain('Approve the plan');
    [...el.querySelectorAll('button')].find((b) => b.textContent?.trim() === 'Approve')?.click();
    expect(api.resume).toHaveBeenCalledWith('r1', { action: 'approve', interrupt_id: 'i1' });
    expect(api.getRun).toHaveBeenCalledTimes(2); // refreshed after resuming
  });

  it('refreshes (debounced) on meaningful events only', fakeAsync(() => {
    api.getRun.calls.reset();
    onEvent({ id: 1, run_id: 'r1', type: 'tool_call', node: 'developer', task_id: 'T1', payload: {}, created_at: '' });
    tick(300);
    expect(api.getRun).not.toHaveBeenCalled();
    for (const id of [2, 3, 4]) {
      onEvent({ id, run_id: 'r1', type: 'status', node: null, task_id: null, payload: { status: 'designing' }, created_at: '' });
    }
    tick(300);
    expect(api.getRun).toHaveBeenCalledTimes(1);
  }));

  it('closes the stream once the run is finished', () => {
    api.getRun.and.returnValue(of({ ...RUN, status: 'completed', pending: [] }));
    onEvent({ id: 9, run_id: 'r1', type: 'status', node: null, task_id: null, payload: { status: 'completed' }, created_at: '' });
    fixture.componentInstance['load']();
    expect(stream.close).toHaveBeenCalled();
  });
});
