import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideNoopAnimations } from '@angular/platform-browser/animations';
import { of } from 'rxjs';

import { ResumeRequest, RunDetail } from '../../core/api.models';
import { ApiService } from '../../core/api.service';
import { wfNode } from './run-detail.component.spec';
import { WorkflowPanelComponent } from './workflow-panel.component';

const RUN: RunDetail = {
  id: 'r1', request: '# Shop\n\n- products\n- orders', repo_target: 'me/shop', create_repo: false,
  status: 'awaiting_design_approval', pr_url: null, error: null, created_at: null, updated_at: null,
  busy: false, plan: { summary: 'Shop', user_stories: [], tasks: [] },
  design: {
    stack: 'python', template_id: 'python-fastapi', components: [], project_structure: [],
    modules: [{ name: 'orders', path: 'app/orders.py', responsibility: 'orders', interface: 'POST /orders' }],
    key_decisions: [], design_doc: '# Design',
    plan_assessment: { concerns: ['No stock locking'], assumptions: [], suggested_changes: ['Split T3'] },
  },
  tasks: {}, qa_log: [], integration: null, integration_branch: null, wave: 0, errors: [],
  pending: [{ interrupt_id: 'd1', kind: 'approval', title: 'Approve the design', artifact: 'design',
              allowed_actions: ['approve', 'reject', 'edit'], data: {}, error: null }],
};

describe('WorkflowPanelComponent', () => {
  let fixture: ComponentFixture<WorkflowPanelComponent>;
  const el = () => fixture.nativeElement as HTMLElement;

  function setup(node = wfNode('approve_design', { kind: 'approval', label: 'Design approval', status: 'waiting', pending_interrupt_ids: ['d1'] })) {
    const api = jasmine.createSpyObj<ApiService>('ApiService', ['diff']);
    api.diff.and.returnValue(of({ task_id: 'T1', base: 'a', head: 'b', diff: '', truncated: false }));
    TestBed.configureTestingModule({
      imports: [WorkflowPanelComponent],
      providers: [provideNoopAnimations(), { provide: ApiService, useValue: api }],
    });
    fixture = TestBed.createComponent(WorkflowPanelComponent);
    fixture.componentRef.setInput('node', node);
    fixture.componentRef.setInput('run', RUN);
    fixture.detectChanges();
  }

  it('shows the design with the plan assessment and the approval actions', () => {
    setup();
    const sent: ResumeRequest[] = [];
    fixture.componentInstance.resumeRequested.subscribe((r) => sent.push(r));
    expect(el().querySelector('h2')?.textContent).toBe('Design approval');
    expect(el().querySelector('.assessment')?.textContent).toContain('No stock locking');
    expect(el().querySelector('.assessment')?.textContent).toContain('Split T3');
    [...el().querySelectorAll('button')].find((b) => b.textContent?.trim() === 'Approve')?.click();
    expect(sent).toEqual([{ action: 'approve', interrupt_id: 'd1' }]);
  });

  it('renders the requirements document and the activity feed, newest first', () => {
    setup(wfNode('requirements', {
      kind: 'input', label: 'Requirements', status: 'done',
      activity: [
        { at: '2026-09-26T10:00:00Z', kind: 'tool_call', text: 'first', ok: null },
        { at: '2026-09-26T10:00:05Z', kind: 'error', text: 'second', ok: false },
      ],
    }));
    expect(el().querySelector('.doc h1')?.textContent).toBe('Shop');
    expect(el().querySelectorAll('.doc li').length).toBe(2);
    const items = [...el().querySelectorAll('.activity li')];
    expect(items.map((li) => li.textContent)).toEqual([jasmine.stringContaining('second'), jasmine.stringContaining('first')]);
    expect(items[0].classList).toContain('bad');
    expect(el().querySelector('app-action-panel')).toBeNull(); // nothing pending here
  });

  it('shows gate results and the repository summary', () => {
    const run: RunDetail = {
      ...RUN, target: 'existing', mode: 'quick',
      repo_info: {
        base_branch: 'master', source: 'detected', notes: ['legacy/: Gradle builds are not supported'],
        file_count: 42, tree: '', readme: '# Shop', commit: 'abc',
        projects: [{ stack: 'python', path: '.', install_cmd: 'pip', build_cmd: '', test_cmd: 'pytest -q', coverage_cmd: null }],
      },
      gates: {
        round: 1,
        results: [
          { name: 'secrets', status: 'failed', summary: '1 secret(s) in changed files', details: ['app/x.py:3 (aws)'], allowable: false },
          { name: 'coverage', status: 'passed', summary: 'coverage ok', details: ['python: 81.0%'], allowable: true },
        ],
      },
    };
    setup(wfNode('gates', { label: 'Quality gates', status: 'failed' }));
    fixture.componentRef.setInput('run', run);
    fixture.detectChanges();
    const items = [...el().querySelectorAll('.gates > li')];
    expect(items.map((li) => li.className)).toEqual(['failed', 'passed']);
    expect(items[0].textContent).toContain('cannot be allowed');
    expect(items[0].textContent).toContain('app/x.py:3 (aws)');
    expect(el().textContent).toContain('After 1 automatic fix round(s)');

    fixture.componentRef.setInput('node', wfNode('prepare_repo', { label: 'Repository', status: 'done' }));
    fixture.detectChanges();
    expect(el().textContent).toContain('Base branch master');
    expect(el().textContent).toContain('quick fix mode');
    expect(el().querySelector('.grid td code')?.textContent).toBe('.');
    expect(el().querySelector('.warn')?.textContent).toContain('Gradle');
    expect(el().querySelector('.doc h1')?.textContent).toBe('Shop');
  });

  it('emits close', () => {
    setup();
    let closed = false;
    fixture.componentInstance.closed.subscribe(() => (closed = true));
    (el().querySelector('button.close') as HTMLButtonElement).click();
    expect(closed).toBeTrue();
  });
});
