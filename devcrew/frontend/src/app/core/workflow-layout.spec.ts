import { WorkflowEdge, WorkflowNode } from './api.models';
import {
  GROUP_PREFIX,
  STAGE_SIZE,
  TASK_SIZE,
  compactWorkflow,
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
    expect(iconKind(node('prepare_repo'))).toBe('repository');
    expect(iconKind(node('gates'))).toBe('gates');
    expect(iconKind(node('scaffold'))).toBe('system');
    expect(iconKind(node('followup:2', { kind: 'agent' }))).toBe('coordinator');
    expect(iconKind(node('push:2', { kind: 'output' }))).toBe('delivery');
    expect(iconKind(node('watch'))).toBe('watch');
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

describe('compact workflow', () => {
  const done = { status: 'done' as const };
  const stages = ['requirements', 'planner', 'approve_plan', 'architect', 'approve_design', 'scaffold'];
  const chain = (ids: string[]) => ids.slice(1).map((id, i) => edge(ids[i], id));

  it('folds finished stages, finished waves and finished PR rounds', () => {
    const nodes = [
      ...stages.map((id) => node(id, done)),
      node('task:T1', done), node('task:T2', done), node('task:T3', { status: 'skipped' }),
      node('task:T4', { status: 'running' }), node('task:T5', done),
      node('integration'), node('followup:1', done), node('push:1', done), node('followup:2', { status: 'running' }),
    ];
    const edges = [
      ...chain(stages), edge('approve_plan', 'planner', 'loop'),
      edge('scaffold', 'task:T1'), edge('scaffold', 'task:T2'), edge('scaffold', 'task:T3'),
      edge('task:T1', 'task:T4'), edge('task:T2', 'task:T5'),
      edge('task:T4', 'integration'), edge('task:T5', 'integration'),
      edge('integration', 'followup:1'), edge('followup:1', 'push:1'), edge('push:1', 'followup:2'),
    ];
    const view = compactWorkflow(nodes, edges);
    const ids = view.nodes.map((n) => n.id);
    expect(ids).toEqual([
      `${GROUP_PREFIX}stages`, `${GROUP_PREFIX}wave:1`, 'task:T4', 'task:T5', 'integration',
      `${GROUP_PREFIX}round:1`, 'followup:2',
    ]);
    expect(view.groups.get(`${GROUP_PREFIX}stages`)).toEqual(stages);
    expect(view.groups.get(`${GROUP_PREFIX}wave:1`)).toEqual(['task:T1', 'task:T2', 'task:T3']);
    const wave = view.nodes[1];
    expect(wave.label).toBe('Wave 1 · 3 tasks');
    expect(wave.detail).toBe('2 merged, 1 skipped');
    // a wave with running work stays open; edges are rewired and de-duplicated
    const pairs = view.edges.map((e) => `${e.source}>${e.target}`);
    expect(pairs).toEqual([
      `${GROUP_PREFIX}stages>${GROUP_PREFIX}wave:1`, `${GROUP_PREFIX}wave:1>task:T4`, `${GROUP_PREFIX}wave:1>task:T5`,
      'task:T4>integration', 'task:T5>integration', `integration>${GROUP_PREFIX}round:1`, `${GROUP_PREFIX}round:1>followup:2`,
    ]);
  });

  it('folds by flow order, not list order: stages after the tasks form their own group', () => {
    // the backend lists every stage first and the task nodes last
    const after = ['integration', 'gates', 'approve_final', 'delivery'];
    const nodes = [
      ...stages.map((id) => node(id, done)),
      ...after.map((id) => node(id, id === 'delivery' ? { status: 'running' } : done)),
      node('task:T1', done), node('task:T2', done),
    ];
    const edges = [
      ...chain(stages), ...chain(after),
      edge('scaffold', 'task:T1'), edge('scaffold', 'task:T2'),
      edge('task:T1', 'integration'), edge('task:T2', 'integration'),
    ];
    const view = compactWorkflow(nodes, edges);
    expect(view.groups.get(`${GROUP_PREFIX}stages`)).toEqual(stages);
    expect(view.groups.get(`${GROUP_PREFIX}checks`)).toEqual(['integration', 'gates', 'approve_final']);
    expect(view.edges.map((e) => `${e.source}>${e.target}`)).toEqual([
      `${GROUP_PREFIX}checks>delivery`, `${GROUP_PREFIX}stages>${GROUP_PREFIX}wave:1`, `${GROUP_PREFIX}wave:1>${GROUP_PREFIX}checks`,
    ]);
    // no cycle: every edge goes left to right
    const r = ranks(view.nodes, view.edges);
    expect(view.edges.every((e) => (r.get(e.source) ?? 0) < (r.get(e.target) ?? 0))).toBeTrue();
  });

  it('keeps opened groups and the selected node unfolded', () => {
    const nodes = stages.map((id) => node(id, done));
    expect(compactWorkflow(nodes, chain(stages), new Set([`${GROUP_PREFIX}stages`])).nodes.length).toBe(6);
    const kept = compactWorkflow(nodes, chain(stages), new Set(['approve_design']));
    expect(kept.nodes.map((n) => n.id)).toEqual([`${GROUP_PREFIX}stages`, 'approve_design', 'scaffold']);
  });

  it('leaves short or unfinished workflows alone', () => {
    const nodes = [node('requirements', done), node('planner', done), node('approve_plan', { status: 'waiting' })];
    const view = compactWorkflow(nodes, chain(nodes.map((n) => n.id)));
    expect(view.nodes).toEqual(nodes);
    expect(view.groups.size).toBe(0);
  });
});
