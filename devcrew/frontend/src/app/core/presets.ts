import { RunMode, RunTarget } from './api.models';

/** Saved New run settings (everything but the requirements), kept in this browser. */
export interface RunPreset {
  name: string;
  repo_target: string;
  target: RunTarget;
  mode: RunMode;
  create_repo: boolean;
  token_budget: number | null;
  time_budget_min: number | null;
  models: Record<string, string>;
}

const KEY = 'devcrew.presets';

function storage(): Storage | null {
  try {
    return typeof localStorage === 'undefined' ? null : localStorage;
  } catch {
    return null;
  }
}

export function loadPresets(): RunPreset[] {
  try {
    const raw = storage()?.getItem(KEY);
    const data = raw ? (JSON.parse(raw) as unknown) : [];
    return Array.isArray(data)
      ? (data as RunPreset[]).filter((p) => p && typeof p.name === 'string' && p.name.trim())
      : [];
  } catch {
    return [];
  }
}

function store(presets: RunPreset[]): void {
  try {
    storage()?.setItem(KEY, JSON.stringify(presets));
  } catch {
    // private mode or full storage: presets just don't persist
  }
}

/** Adds or replaces (by name) a preset; returns the new list, sorted by name. */
export function savePreset(preset: RunPreset): RunPreset[] {
  const name = preset.name.trim();
  const presets = [...loadPresets().filter((p) => p.name !== name), { ...preset, name }].sort((a, b) =>
    a.name.localeCompare(b.name),
  );
  store(presets);
  return presets;
}

export function deletePreset(name: string): RunPreset[] {
  const presets = loadPresets().filter((p) => p.name !== name);
  store(presets);
  return presets;
}
