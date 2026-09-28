import { Injectable, inject, signal } from '@angular/core';

import { EVENT_TYPES, RunEvent, RunStatus, TERMINAL_STATUSES } from './api.models';
import { ApiService } from './api.service';

export type StreamState = 'connecting' | 'open' | 'reconnecting' | 'closed';

/** Minimal EventSource surface, so tests can inject a fake. */
export interface EventSourceLike {
  readonly readyState: number;
  onopen: ((ev: Event) => unknown) | null;
  onerror: ((ev: Event) => unknown) | null;
  addEventListener(type: string, listener: (ev: MessageEvent<string>) => void): void;
  close(): void;
}

export type EventSourceFactory = (url: string) => EventSourceLike;

const CLOSED = 2;
const MAX_BACKOFF_MS = 15_000;

/**
 * One live SSE connection to a run's event stream, exposed as signals.
 *
 * - Events are appended in id order and de-duplicated by id.
 * - The browser's EventSource reconnects by itself (sending Last-Event-ID); if it gives up
 *   (connection closed with an error), we reconnect with exponential backoff and pass
 *   `?last_event_id=` so the backend replays only what was missed.
 * - The stream closes itself after a terminal status event (completed / failed / cancelled).
 */
export class RunEventStream {
  readonly events = signal<RunEvent[]>([]);
  readonly state = signal<StreamState>('connecting');
  readonly lastEventId = signal<number | null>(null);

  private source: EventSourceLike | null = null;
  private retryTimer: ReturnType<typeof setTimeout> | null = null;
  private attempts = 0;
  private closedByUser = false;

  constructor(
    private readonly url: (lastEventId: number | null) => string,
    private readonly factory: EventSourceFactory,
    private readonly onEvent: (event: RunEvent) => void = () => undefined,
  ) {}

  open(): void {
    this.closedByUser = false;
    this.connect();
  }

  close(): void {
    this.closedByUser = true;
    this.clearTimer();
    this.source?.close();
    this.source = null;
    this.state.set('closed');
  }

  private connect(): void {
    this.source?.close();
    const source = this.factory(this.url(this.lastEventId()));
    this.source = source;
    this.state.set(this.attempts === 0 ? 'connecting' : 'reconnecting');
    source.onopen = () => {
      this.attempts = 0;
      this.state.set('open');
    };
    source.onerror = () => {
      if (this.closedByUser) {
        return;
      }
      if (source.readyState === CLOSED) {
        this.scheduleReconnect();
      } else {
        this.state.set('reconnecting'); // the browser retries on its own
      }
    };
    for (const type of EVENT_TYPES) {
      source.addEventListener(type, (message) => this.handle(message));
    }
  }

  private handle(message: MessageEvent<string>): void {
    let event: RunEvent;
    try {
      event = JSON.parse(message.data) as RunEvent;
    } catch {
      return;
    }
    const last = this.lastEventId();
    if (last !== null && event.id <= last) {
      return; // replayed twice (reconnect overlap)
    }
    this.lastEventId.set(event.id);
    this.events.update((events) => [...events, event]);
    this.onEvent(event);
    if (event.type === 'status' && TERMINAL_STATUSES.includes(event.payload['status'] as RunStatus)) {
      this.close();
    }
  }

  private scheduleReconnect(): void {
    this.source?.close();
    this.source = null;
    this.clearTimer();
    const delay = Math.min(1000 * 2 ** this.attempts, MAX_BACKOFF_MS);
    this.attempts += 1;
    this.state.set('reconnecting');
    this.retryTimer = setTimeout(() => this.connect(), delay);
  }

  private clearTimer(): void {
    if (this.retryTimer !== null) {
      clearTimeout(this.retryTimer);
      this.retryTimer = null;
    }
  }
}

/** Creates signal-based SSE streams for runs. */
@Injectable({ providedIn: 'root' })
export class RunEventsService {
  private readonly api = inject(ApiService);
  factory: EventSourceFactory = (url) => new EventSource(url);

  connect(runId: string, onEvent?: (event: RunEvent) => void): RunEventStream {
    const stream = new RunEventStream(
      (last) => this.api.eventsUrl(runId, last),
      this.factory,
      onEvent,
    );
    stream.open();
    return stream;
  }
}
