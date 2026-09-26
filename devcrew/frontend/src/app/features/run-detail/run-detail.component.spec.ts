import { signal } from '@angular/core';
import { ComponentFixture, TestBed, fakeAsync, tick } from '@angular/core/testing';
import { provideNoopAnimations } from '@angular/platform-browser/animations';
import { provideRouter } from '@angular/router';
import { of } from 'rxjs';

import { RunDetail, RunEvent, RunMessage, Workflow, WorkflowNode } from '../../core/api.models';
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
    api = jasmine.createSpyObj<ApiService>('ApiService', [
      'getRun', 'workflow', 'resume', 'cancel', 'diff', 'listFiles', 'messages', 'sendMessage', 'pause',
    ]);
    api.getRun.and.returnValue(of(RUN));
    api.messages.and.returnValue(of([]));
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

  it('pauses and resumes the run', () => {
    const el = fixture.nativeElement as HTMLElement;
    const button = (label: string) =>
      [...el.querySelectorAll('.controls button')].find((b) => b.textContent?.trim() === label) as HTMLButtonElement | undefined;
    api.pause.and.returnValue(of({ status: 'executing', pause_requested: true }));
    api.getRun.and.returnValue(of({ ...RUN, status: 'executing', pending: [], pause_requested: true }));
    button('Pause')?.click();
    fixture.detectChanges();
    expect(api.pause).toHaveBeenCalledWith('r1', true);
    expect(el.querySelector('.paused')?.textContent).toContain('Pausing after the current tasks finish');
    expect(button("Don't pause")).toBeDefined();

    api.getRun.and.returnValue(of({ ...RUN, status: 'paused', pending: [], pause_requested: true }));
    fixture.componentInstance['load']();
    fixture.detectChanges();
    expect(el.querySelector('.paused')?.textContent).toContain('Paused before the next wave');
    api.pause.and.returnValue(of({ status: 'executing', pause_requested: false }));
    button('Resume')?.click();
    expect(api.pause).toHaveBeenCalledWith('r1', false);

    // no safe point is left once development is over
    api.getRun.and.returnValue(of({ ...RUN, status: 'awaiting_final_approval', pending: [] }));
    fixture.componentInstance['load']();
    fixture.detectChanges();
    expect(button('Pause')).toBeUndefined();
  });

  it('loads and sends chat messages', () => {
    const sent: RunMessage = {
      id: 7, task_id: null, text: 'use UUIDs', status: 'pending', action: null, reply: null,
      created_at: null, updated_at: null,
    };
    api.sendMessage.and.returnValue(of(sent));
    const c = fixture.componentInstance;
    c.sendMessage({ text: 'use UUIDs', task_id: null });
    expect(api.sendMessage).toHaveBeenCalledWith('r1', { text: 'use UUIDs', task_id: null });
    expect(c.messages()).toEqual([sent]);
    api.messages.and.returnValue(of([{ ...sent, status: 'applied', action: 'note', reply: 'Noted.' }]));
    c['load']();
    expect(c.messages()[0].reply).toBe('Noted.');
  });
});
