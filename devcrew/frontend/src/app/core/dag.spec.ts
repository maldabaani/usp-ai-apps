import { Plan, PlanTask, TaskState } from './api.models';
import { dagColumns, planLayers } from './dag';

const task = (id: string, deps: string[] = []): PlanTask => ({
  id, title: id, description: '', target_files: [], depends_on: deps, stack: 'python', story_ids: [],
});
const state = (id: string, extra: Partial<TaskState> = {}): TaskState => ({
  id, status: 'pending', branch: null, iterations: 0, review: null, test_results: null, feedback: null,
  commit: null, error: null, worktree: null, wave: null, lane: null, conflict_files: [],
  conflict_rounds: 0, coordinator_actions: 0, ...extra,
});
const plan: Plan = { summary: '', user_stories: [], tasks: [task('A'), task('B'), task('C'), task('D', ['A', 'B'])] };

describe('dag', () => {
  it('computes topological layers', () => {
    expect(planLayers(plan.tasks)).toEqual([['A', 'B', 'C'], ['D']]);
  });

  it('shows planned layers before anything is dispatched', () => {
    const cols = dagColumns(plan, {});
    expect(cols.map((c) => c.title)).toEqual(['Layer 1', 'Layer 2']);
  });

  it('places dispatched tasks in their wave lanes, then the planned rest', () => {
    const tasks = {
      A: state('A', { status: 'merged', wave: 1, lane: 1 }),
      B: state('B', { status: 'in_progress', wave: 1, lane: 0 }),
      C: state('C'),
      D: state('D'),
    };
    const cols = dagColumns(plan, tasks);
    expect(cols.map((c) => c.title)).toEqual(['Wave 1', 'Planned 1']);
    expect(cols[0].cards.map((c) => c.id)).toEqual(['B', 'A']); // lane order
    expect(cols[1].cards.map((c) => c.id)).toEqual(['C', 'D']); // D's deps are dispatched
  });

  it('keeps split tasks visible in their wave', () => {
    const split: Plan = { ...plan, tasks: [task('A-1'), task('A-2', ['A-1'])] };
    const cols = dagColumns(split, { A: state('A', { status: 'split', wave: 1, lane: 0 }), 'A-1': state('A-1'), 'A-2': state('A-2') });
    expect(cols[0].cards[0]).toEqual(jasmine.objectContaining({ id: 'A', title: 'A' }));
    expect(cols.slice(1).map((c) => c.cards.map((x) => x.id))).toEqual([['A-1'], ['A-2']]);
  });
});
