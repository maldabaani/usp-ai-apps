import { environment } from '../../environments/environment';
import { loadRuntimeConfig, runtimeConfig, setRuntimeConfig } from './runtime-config';

describe('runtime config', () => {
  afterEach(() => setRuntimeConfig({ apiUrl: environment.apiUrl }));

  const respond = (body: unknown, ok = true) => () =>
    Promise.resolve({ ok, json: () => Promise.resolve(body) } as Response);

  it('reads the API URL from config.json', async () => {
    const config = await loadRuntimeConfig(respond({ apiUrl: ' http://devcrew.lan:9000 ' }));
    expect(config.apiUrl).toBe('http://devcrew.lan:9000');
    expect(runtimeConfig().apiUrl).toBe('http://devcrew.lan:9000');
  });

  it('keeps the build-time default when the file is missing or invalid', async () => {
    expect((await loadRuntimeConfig(respond({}, false))).apiUrl).toBe(environment.apiUrl);
    expect((await loadRuntimeConfig(respond({ apiUrl: 42 }))).apiUrl).toBe(environment.apiUrl);
    expect((await loadRuntimeConfig(() => Promise.reject(new Error('offline')))).apiUrl).toBe(environment.apiUrl);
  });
});
