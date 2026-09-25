/** Task DAG layout: planned layers and actual parallel lanes (waves). */

import { Plan, PlanTask, TaskState } from './api.models';

export interface DagCard {
  id: string;
  title: string;
  stack: string;
  dependsOn: string[];
  state: TaskState | null;
}

export interface DagColumn {
  key: string;
  title: string;
  cards: DagCard[];
}

/** Topological layers: tasks in the same layer can run in parallel. */
export function planLayers(tasks: readonly PlanTask[]): string[][] {
  const ids = new Set(tasks.map((t) => t.id));
  const remaining = new Map(tasks.map((t) => [t.id, t.depends_on]));
  const done = new Set<string>();
  const layers: string[][] = [];
  while (remaining.size > 0) {
    // Dependencies outside `tasks` (e.g. already dispatched) count as satisfied.
    const ready = [...remaining.entries()]
      .filter(([, deps]) => deps.every((d) => done.has(d) || !ids.has(d)))
      .map(([id]) => id)
      .sort();
    if (ready.length === 0) {
      layers.push([...remaining.keys()].sort()); // cycle guard: never loop forever
      break;
    }
    layers.push(ready);
    for (const id of ready) {
      done.add(id);
      remaining.delete(id);
    }
  }
  return layers;
}

/**
 * Columns for the DAG view. Dispatched tasks are placed in their wave (columns "Wave 1",
 * "Wave 2", ...) at their lane; tasks not dispatched yet follow as "Planned" layers.
 * Tasks replaced by a split (no longer in the plan) stay visible in their wave.
 */
export function dagColumns(plan: Plan | null, tasks: Record<string, TaskState>): DagColumn[] {
  const planTasks = plan?.tasks ?? [];
  const byId = new Map(planTasks.map((t) => [t.id, t]));
  const card = (id: string): DagCard => {
    const task = byId.get(id);
    return {
      id,
      title: task?.title ?? id,
      stack: task?.stack ?? '',
      dependsOn: task?.depends_on ?? [],
      state: tasks[id] ?? null,
    };
  };

  const waves = new Map<number, DagCard[]>();
  for (const state of Object.values(tasks)) {
    if (state.wave !== null && state.wave !== undefined && state.status !== 'pending') {
      const list = waves.get(state.wave) ?? [];
      list.push(card(state.id));
      waves.set(state.wave, list);
    }
  }
  const columns: DagColumn[] = [...waves.entries()]
    .sort(([a], [b]) => a - b)
    .map(([wave, cards]) => ({
      key: `wave-${wave}`,
      title: `Wave ${wave}`,
      cards: cards.sort((a, b) => (a.state?.lane ?? 0) - (b.state?.lane ?? 0)),
    }));

  const placed = new Set(columns.flatMap((c) => c.cards.map((x) => x.id)));
  const queued = planTasks.filter((t) => !placed.has(t.id));
  const dispatched = columns.length;
  planLayers(queued).forEach((layer, i) =>
    columns.push({
      key: `planned-${i}`,
      title: dispatched ? `Planned ${i + 1}` : `Layer ${i + 1}`,
      cards: layer.map(card),
    }),
  );
  return columns;
}
