import { signal } from '@angular/core';
import { ComponentFixture, TestBed, fakeAsync, tick } from '@angular/core/testing';
import { provideNoopAnimations } from '@angular/platform-browser/animations';
import { provideRouter } from '@angular/router';
import { of } from 'rxjs';

import { RunDetail, RunEvent, Workflow, WorkflowNode } from '../../core/api.models';
import { ApiService } from '../../core/api.service';
import { RunEventStream, RunEventsService } from '../../core/run-events.service';
import { RunDetailComponent } from './run-detail.component';

const RUN: RunDetail = {
  id: 'r1', request: '# TODO API\n\nBuild a TODO API', repo_target: 'me/todo', create_repo: false,
  status: 'awaiting_plan_approval', pr_url: null, error: null, created_at: null, updated_at: null,
  busy: false, plan: { summary: 'TODO', user_stories: [], tasks: [] }, design: null, tasks: {},
  qa_log: [], integration: null, integration_branch: null, wave: 0, errors: [],
  pending: [{ interrupt_id: 'i1', kind: 'approval', title: 'Approve the plan', artifact: 'plan',
              allowed_actions: ['approve', 'reject', 'edit'], data: {}, error: null }],
};

export function wfNode(id: string, patch: Partial<WorkflowNode> = {}): WorkflowNode {
  return {
    id, kind: 'system', label: id, status: 'pending', detail: null, step: null, task_id: null,
    stack: null, started_at: null, finished_at: null, runs: 0, counters: {},
    pending_interrupt_ids: [], activity: [], ...patch,
  };
}

const WORKFLOW: Workflow = {
  run_id: 'r1',
  status: 'awaiting_plan_approval',
  nodes: [
    wfNode('requirements', { kind: 'input', label: 'Requirements', status: 'done' }),
    wfNode('planner', { kind: 'agent', label: 'Planner', status: 'done' }),
    wfNode('approve_plan', { kind: 'approval', label: 'Plan approval', status: 'waiting', pending_interrupt_ids: ['i1'] }),
  ],
  edges: [
    { id: 'requirements->planner', source: 'requirements', target: 'planner', kind: 'flow', active: false, label: null },
    { id: 'planner->approve_plan', source: 'planner', target: 'approve_plan', kind: 'flow', active: true, label: null },
  ],
  attention: ['approve_plan'],
};

describe('RunDetailComponent', () => {
  let fixture: ComponentFixture<RunDetailComponent>;
  let api: jasmine.SpyObj<ApiService>;
  let onEvent: (e: RunEvent) => void;
  let stream: jasmine.SpyObj<RunEventStream>;
  const event = (id: number, type: RunEvent['type']): RunEvent =>
    ({ id, run_id: 'r1', type, node: 'developer', task_id: 'T1', payload: {}, created_at: '' });

  beforeEach(() => {
    api = jasmine.createSpyObj<ApiService>('ApiService', ['getRun', 'workflow', 'resume', 'cancel', 'diff', 'listFiles']);
    api.getRun.and.returnValue(of(RUN));
    api.workflow.and.returnValue(of(WORKFLOW));
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

  it('shows the workflow and opens the waiting approval in the side panel', () => {
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('h1')?.textContent).toBe('TODO API'); // title from the document heading
    expect(el.querySelector('app-workflow-graph')).not.toBeNull();
    expect(el.querySelector('.attention')?.textContent).toContain('Plan approval');
    expect(fixture.componentInstance.selectedNode()).toBe('approve_plan');
    const panel = el.querySelector('app-workflow-panel') as HTMLElement;
    expect(panel.textContent).toContain('Approve the plan');
    [...panel.querySelectorAll('button')].find((b) => b.textContent?.trim() === 'Approve')?.click();
    expect(api.resume).toHaveBeenCalledWith('r1', { action: 'approve', interrupt_id: 'i1' });
    expect(api.getRun).toHaveBeenCalledTimes(2); // refreshed after resuming
    expect(api.workflow).toHaveBeenCalledTimes(2);
  });

  it('closing the panel keeps it closed until something new needs attention', () => {
    fixture.componentInstance.pick(null); // the panel's close button
    fixture.componentInstance['load']();
    expect(fixture.componentInstance.selectedNode()).toBeNull();
    api.workflow.and.returnValue(of({ ...WORKFLOW, attention: ['approve_plan', 'task:T1'] }));
    fixture.componentInstance['load']();
    expect(fixture.componentInstance.selectedNode()).toBe('task:T1');
  });

  it('an auto-opened panel follows the work; a user-picked node stays', () => {
    const c = fixture.componentInstance;
    expect(c.selectedNode()).toBe('approve_plan');
    const moved: Workflow = {
      ...WORKFLOW, status: 'designing', attention: [],
      nodes: [
        ...WORKFLOW.nodes.map((n) => (n.id === 'approve_plan' ? { ...n, status: 'done' as const, pending_interrupt_ids: [] } : n)),
        wfNode('architect', { kind: 'agent', label: 'Architect', status: 'running' }),
      ],
    };
    api.workflow.and.returnValue(of(moved));
    c['load']();
    expect(c.selectedNode()).toBe('architect');
    c.pick('planner');
    c['load']();
    expect(c.selectedNode()).toBe('planner'); // the user's choice is kept
  });

  it('batches refreshes while events stream in', fakeAsync(() => {
    api.getRun.calls.reset();
    api.workflow.calls.reset();
    for (const id of [1, 2, 3]) {
      onEvent(event(id, 'tool_call'));
    }
    tick(100);
    onEvent(event(4, 'tool_result'));
    tick(300);
    expect(api.getRun).toHaveBeenCalledTimes(1);
    expect(api.workflow).toHaveBeenCalledTimes(1);
    onEvent(event(5, 'status'));
    tick(300);
    expect(api.workflow).toHaveBeenCalledTimes(2);
  }));

  it('closes the stream once the run is finished', () => {
    api.getRun.and.returnValue(of({ ...RUN, status: 'completed', pending: [] }));
    fixture.componentInstance['load']();
    expect(stream.close).toHaveBeenCalled();
  });
});
