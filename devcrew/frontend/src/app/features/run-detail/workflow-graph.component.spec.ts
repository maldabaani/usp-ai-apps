import { ComponentFixture, TestBed } from '@angular/core/testing';

import { Workflow } from '../../core/api.models';
import { wfNode } from './run-detail.component.spec';
import { WorkflowGraphComponent, focusNodes } from './workflow-graph.component';

const WF: Workflow = {
  run_id: 'r1',
  status: 'executing',
  nodes: [
    wfNode('scaffold', { label: 'Scaffold', status: 'done' }),
    wfNode('task:T1', { kind: 'task', label: 'T1 · Model', status: 'running', step: 'reviewer',
                        detail: 'reviewer · iteration 1/3', task_id: 'T1', started_at: new Date().toISOString() }),
    wfNode('task:T2', { kind: 'task', label: 'T2 · Router', status: 'waiting', step: 'ask_human',
                        task_id: 'T2', pending_interrupt_ids: ['q1'], counters: { coordinator_actions: 1 } }),
  ],
  edges: [
    { id: 'scaffold->task:T1', source: 'scaffold', target: 'task:T1', kind: 'flow', active: true, label: null },
    { id: 'scaffold->task:T2', source: 'scaffold', target: 'task:T2', kind: 'flow', active: true, label: null },
  ],
  attention: ['task:T2'],
};

describe('WorkflowGraphComponent', () => {
  let fixture: ComponentFixture<WorkflowGraphComponent>;
  const el = () => fixture.nativeElement as HTMLElement;
  const nodeEl = (id: string) => el().querySelector(`[data-node="${id}"]`) as HTMLElement;

  beforeEach(async () => {
    TestBed.configureTestingModule({ imports: [WorkflowGraphComponent] });
    fixture = TestBed.createComponent(WorkflowGraphComponent);
    fixture.componentRef.setInput('workflow', WF);
    fixture.componentRef.setInput('selected', 'task:T1');
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
  });

  it('renders one node per stage/task with status, step and badges', () => {
    expect(fixture.componentInstance.nodes().length).toBe(3);
    const t1 = nodeEl('task:T1');
    expect(t1.classList).toContain('running');
    expect(t1.classList).toContain('selected');
    expect(t1.textContent).toContain('working');
    expect(t1.querySelector('.step.on')?.textContent).toBe('review');
    expect(t1.querySelector('.step.passed')?.textContent).toBe('dev');
    expect(t1.querySelector('app-agent-icon svg')?.getAttribute('aria-label')).toBe('reviewer icon');
    const t2 = nodeEl('task:T2');
    expect(t2.classList).toContain('waiting');
    expect(t2.querySelector('.badge')).not.toBeNull();
    expect(t2.querySelector('.coord')?.textContent).toContain('1');
    expect(fixture.componentInstance.edges().map((e) => e.id)).toEqual(WF.edges.map((e) => e.id));
  });

  it('emits the clicked node and updates in place without re-layout', () => {
    const picked: string[] = [];
    fixture.componentInstance.nodeSelected.subscribe((id) => picked.push(id));
    nodeEl('task:T2').click();
    expect(picked).toEqual(['task:T2']);

    const before = fixture.componentInstance.nodes();
    fixture.componentRef.setInput('workflow', {
      ...WF,
      nodes: WF.nodes.map((n) => (n.id === 'task:T1' ? { ...n, status: 'done' as const, step: null } : n)),
    });
    fixture.detectChanges();
    expect(fixture.componentInstance.nodes()).toBe(before); // same node objects: no re-layout
    expect(nodeEl('task:T1').classList).toContain('done');
  });

  it('rebuilds the graph when tasks are added', () => {
    const before = fixture.componentInstance.nodes();
    fixture.componentRef.setInput('workflow', {
      ...WF, nodes: [...WF.nodes, wfNode('task:T3', { kind: 'task', label: 'T3' })],
    });
    fixture.detectChanges();
    expect(fixture.componentInstance.nodes()).not.toBe(before);
    expect(fixture.componentInstance.nodes().length).toBe(4);
  });
});

describe('focusNodes', () => {
  it('frames active nodes with their neighbors', () => {
    expect(focusNodes(WF)).toEqual(['scaffold', 'task:T1', 'task:T2']);
  });

  it('falls back to the last finished node when nothing is active', () => {
    const idle: Workflow = { ...WF, nodes: WF.nodes.map((n) => ({ ...n, status: n.id === 'task:T2' ? 'pending' as const : 'done' as const })) };
    expect(focusNodes(idle)).toEqual(['scaffold', 'task:T1']);
  });
});
