import { environment } from '../../environments/environment';

/** Settings read at startup from `config.json` next to index.html (BL-070), so one built image
 * can point at any backend: the container writes the file from DEVCREW_API_URL. */
export interface RuntimeConfig {
  apiUrl: string;
}

let current: RuntimeConfig = { apiUrl: environment.apiUrl };

export function runtimeConfig(): RuntimeConfig {
  return current;
}

/** Loads config.json; a missing or invalid file keeps the build-time defaults. */
export async function loadRuntimeConfig(
  fetcher: (url: string, init?: RequestInit) => Promise<Response> = (u, i) => fetch(u, i),
): Promise<RuntimeConfig> {
  try {
    const resp = await fetcher('config.json', { cache: 'no-store' });
    if (resp.ok) {
      const data = (await resp.json()) as Partial<RuntimeConfig> | null;
      if (data && typeof data.apiUrl === 'string' && data.apiUrl.trim()) {
        current = { ...current, apiUrl: data.apiUrl.trim() };
      }
    }
  } catch {
    // keep the defaults
  }
  return current;
}

/** Test helper. */
export function setRuntimeConfig(config: RuntimeConfig): void {
  current = config;
}
