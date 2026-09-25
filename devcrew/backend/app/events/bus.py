"""In-process pub/sub for run events.

Guarantees:
- Every event is persisted BEFORE it is fanned out, so anything a live subscriber
  saw can be replayed after a reconnect (SSE Last-Event-ID).
- Per-subscriber delivery is gap-free and in id order: subscribe() registers its
  live queue first, then replays from the store, then de-duplicates live events by id.
- A slow subscriber never blocks publishers: if its bounded queue overflows it is
  marked lagged and transparently resyncs from the store.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any

from app.events.store import EventStore
from app.events.types import Event, EventIn, EventType

logger = logging.getLogger(__name__)

REPLAY_PAGE_SIZE = 500


@dataclass(eq=False)
class _Subscriber:
    queue: asyncio.Queue[Event]
    lagged: bool = False


class EventBus:
    def __init__(self, store: EventStore, *, queue_size: int = 1000) -> None:
        self._store = store
        self._queue_size = queue_size
        self._subscribers: dict[str, set[_Subscriber]] = defaultdict(set)
        # Serializes persist+fan-out so live delivery order always matches id order.
        self._publish_lock = asyncio.Lock()

    async def publish(
        self,
        run_id: str,
        type: EventType,
        *,
        node: str | None = None,
        task_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> Event:
        event_in = EventIn(
            run_id=run_id, type=type, node=node, task_id=task_id, payload=payload or {}
        )
        async with self._publish_lock:
            event = await self._store.append(event_in)
            for sub in self._subscribers.get(run_id, ()):
                try:
                    sub.queue.put_nowait(event)
                except asyncio.QueueFull:
                    sub.lagged = True
        return event

    def subscriber_count(self, run_id: str) -> int:
        return len(self._subscribers.get(run_id, ()))

    async def _replay(self, run_id: str, after_id: int) -> AsyncGenerator[Event]:
        while True:
            page = await self._store.list_after(run_id, after_id, REPLAY_PAGE_SIZE)
            for event in page:
                after_id = event.id
                yield event
            if len(page) < REPLAY_PAGE_SIZE:
                return

    async def subscribe(
        self, run_id: str, last_event_id: int | None = None
    ) -> AsyncGenerator[Event]:
        """Yield all events after `last_event_id` (replayed), then live events, forever.

        The caller stops by breaking out / closing the generator (e.g. on client disconnect).
        """
        sub = _Subscriber(queue=asyncio.Queue(maxsize=self._queue_size))
        self._subscribers[run_id].add(sub)
        last_id = last_event_id or 0
        try:
            async for event in self._replay(run_id, last_id):
                last_id = event.id
                yield event
            while True:
                if sub.lagged:
                    logger.warning("Subscriber for run %s lagged; resyncing from store", run_id)
                    while not sub.queue.empty():
                        sub.queue.get_nowait()
                    sub.lagged = False
                    async for event in self._replay(run_id, last_id):
                        last_id = event.id
                        yield event
                    continue
                event = await sub.queue.get()
                if event.id <= last_id:
                    continue  # already delivered during replay
                last_id = event.id
                yield event
        finally:
            subs = self._subscribers.get(run_id)
            if subs is not None:
                subs.discard(sub)
                if not subs:
                    self._subscribers.pop(run_id, None)
