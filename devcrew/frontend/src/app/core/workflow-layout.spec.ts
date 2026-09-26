import { WorkflowEdge, WorkflowNode } from './api.models';
import {
  STAGE_SIZE,
  TASK_SIZE,
  formatDuration,
  iconKind,
  layoutWorkflow,
  nodeElapsed,
  ranks,
  structureKey,
} from './workflow-layout';

function node(id: string, patch: Partial<WorkflowNode> = {}): WorkflowNode {
  return {
    id, kind: id.startsWith('task:') ? 'task' : 'system', label: id, status: 'pending', detail: null,
    step: null, task_id: null, stack: null, started_at: null, finished_at: null, runs: 0,
    counters: {}, pending_interrupt_ids: [], activity: [], ...patch,
  };
}
const edge = (source: string, target: string, kind: 'flow' | 'loop' = 'flow'): WorkflowEdge =>
  ({ id: `${source}->${target}`, source, target, kind, active: false, label: null });

describe('workflow layout', () => {
  // scaffold -> T1, T2 (parallel) -> T3 -> integration; T2 -> integration; plus a loop edge
  const nodes = ['scaffold', 'task:T1', 'task:T2', 'task:T3', 'integration'].map((id) => node(id));
  const edges = [
    edge('scaffold', 'task:T1'), edge('scaffold', 'task:T2'), edge('task:T1', 'task:T3'),
    edge('task:T3', 'integration'), edge('task:T2', 'integration'), edge('integration', 'scaffold', 'loop'),
  ];

  it('ranks by longest path and ignores loop edges', () => {
    const r = ranks(nodes, edges);
    expect([...r.entries()]).toEqual([
      ['scaffold', 0], ['task:T1', 1], ['task:T2', 1], ['task:T3', 2], ['integration', 3],
    ]);
  });

  it('places parallel tasks in one column, centered around the main row', () => {
    const p = layoutWorkflow(nodes, edges);
    const t1 = p.get('task:T1')!;
    const t2 = p.get('task:T2')!;
    expect(t1.x).toBe(t2.x);
    expect(t1.x).toBeGreaterThan(p.get('scaffold')!.x);
    expect(t1.y + t2.y + TASK_SIZE.height).toBeCloseTo(0, 5); // symmetric around y = 0
    expect(p.get('scaffold')!.y).toBe(-STAGE_SIZE.height / 2);
    expect(p.get('integration')!.x).toBeGreaterThan(p.get('task:T3')!.x);
  });

  it('survives cycles in flow edges', () => {
    const r = ranks([node('a'), node('b')], [edge('a', 'b'), edge('b', 'a')]);
    expect(r.size).toBe(2);
  });

  it('picks the agent icon for the current task step', () => {
    expect(iconKind(node('planner', { kind: 'agent' }))).toBe('planner');
    expect(iconKind(node('task:T1', { step: 'reviewer' }))).toBe('reviewer');
    expect(iconKind(node('task:T1', { step: 'qa' }))).toBe('qa');
    expect(iconKind(node('task:T1', { step: 'escalate' }))).toBe('coordinator');
    expect(iconKind(node('task:T1', { step: 'merge' }))).toBe('developer');
    expect(iconKind(node('approve_plan', { kind: 'approval' }))).toBe('approval');
    expect(iconKind(node('requirements', { kind: 'input' }))).toBe('requirements');
    expect(iconKind(node('delivery', { kind: 'output' }))).toBe('delivery');
  });

  it('formats durations and elapsed times', () => {
    expect(formatDuration(4_200)).toBe('4s');
    expect(formatDuration(125_000)).toBe('2m 05s');
    expect(formatDuration(3_725_000)).toBe('1h 02m');
    const start = '2026-09-26T10:00:00Z';
    const now = Date.parse('2026-09-26T10:01:30Z');
    expect(nodeElapsed(node('a', { status: 'running', started_at: start }), now)).toBe('1m 30s');
    expect(nodeElapsed(node('a', { status: 'done', started_at: start, finished_at: '2026-09-26T10:00:20Z' }), now)).toBe('20s');
    expect(nodeElapsed(node('a', { status: 'pending' }), now)).toBe('');
  });

  it('changes the structure key only when nodes or edges change', () => {
    const key = structureKey(nodes, edges);
    const updated = nodes.map((n) => ({ ...n, status: 'running' as const }));
    expect(structureKey(updated, edges)).toBe(key);
    expect(structureKey([...nodes, node('task:T4')], edges)).not.toBe(key);
  });
});
