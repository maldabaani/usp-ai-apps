import { RunEvent } from './api.models';
import { EventSourceLike, RunEventStream } from './run-events.service';

class FakeEventSource implements EventSourceLike {
  static instances: FakeEventSource[] = [];
  readyState = 0;
  onopen: ((ev: Event) => unknown) | null = null;
  onerror: ((ev: Event) => unknown) | null = null;
  closed = false;
  private listeners = new Map<string, ((ev: MessageEvent<string>) => void)[]>();

  constructor(readonly url: string) {
    FakeEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: (ev: MessageEvent<string>) => void): void {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
  }

  close(): void {
    this.closed = true;
    this.readyState = 2;
  }

  emit(event: Partial<RunEvent> & { id: number; type: RunEvent['type'] }): void {
    const full: RunEvent = { run_id: 'r1', node: null, task_id: null, payload: {}, created_at: '', ...event };
    for (const l of this.listeners.get(event.type) ?? []) {
      l(new MessageEvent(event.type, { data: JSON.stringify(full) }));
    }
  }

  fail(closed: boolean): void {
    this.readyState = closed ? 2 : 0;
    this.onerror?.(new Event('error'));
  }
}

describe('RunEventStream', () => {
  let received: RunEvent[];
  let stream: RunEventStream;
  const url = (last: number | null) => (last === null ? '/events' : `/events?last_event_id=${last}`);

  beforeEach(() => {
    FakeEventSource.instances = [];
    received = [];
    stream = new RunEventStream(url, (u) => new FakeEventSource(u), (e) => received.push(e));
    stream.open();
  });

  afterEach(() => stream.close());

  it('appends events in order and de-duplicates by id', () => {
    const source = FakeEventSource.instances[0];
    source.onopen?.(new Event('open'));
    expect(stream.state()).toBe('open');
    source.emit({ id: 1, type: 'node_started' });
    source.emit({ id: 2, type: 'tool_call' });
    source.emit({ id: 2, type: 'tool_call' }); // replay overlap
    expect(stream.events().map((e) => e.id)).toEqual([1, 2]);
    expect(stream.lastEventId()).toBe(2);
    expect(received.length).toBe(2);
  });

  it('closes itself after a terminal status', () => {
    const source = FakeEventSource.instances[0];
    source.emit({ id: 5, type: 'status', payload: { status: 'awaiting_plan_approval' } });
    expect(stream.state()).not.toBe('closed');
    source.emit({ id: 6, type: 'status', payload: { status: 'completed' } });
    expect(stream.state()).toBe('closed');
    expect(source.closed).toBeTrue();
  });

  it('lets the browser retry while CONNECTING', () => {
    FakeEventSource.instances[0].fail(false);
    expect(stream.state()).toBe('reconnecting');
    expect(FakeEventSource.instances.length).toBe(1);
  });

  it('reconnects with backoff and last_event_id after the connection is closed', () => {
    jasmine.clock().install();
    try {
      const first = FakeEventSource.instances[0];
      first.emit({ id: 41, type: 'merge' });
      first.fail(true);
      expect(stream.state()).toBe('reconnecting');
      jasmine.clock().tick(999);
      expect(FakeEventSource.instances.length).toBe(1);
      jasmine.clock().tick(1);
      const second = FakeEventSource.instances[1];
      expect(second.url).toBe('/events?last_event_id=41');
      second.fail(true);
      jasmine.clock().tick(1999);
      expect(FakeEventSource.instances.length).toBe(2); // backoff doubled to 2s
      jasmine.clock().tick(1);
      expect(FakeEventSource.instances.length).toBe(3);
    } finally {
      jasmine.clock().uninstall();
    }
  });

  it('ignores errors after close()', () => {
    const source = FakeEventSource.instances[0];
    stream.close();
    source.fail(true);
    expect(stream.state()).toBe('closed');
  });
});
