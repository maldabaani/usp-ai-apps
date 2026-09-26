"""Per-run event lists kept in memory and extended incrementally (Phase 15, BL-102/BL-154).

The workflow view, the usage summary and the budget check all read a run's whole event log.
Instead of re-reading it from Postgres on every request (and before every wave), the cache
keeps the list and only fetches the events after the last one it has.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict

from app.events.bus import EventBus
from app.events.types import Event


class EventCache:
    def __init__(self, bus: EventBus, max_runs: int = 64) -> None:
        self._bus = bus
        self._runs: OrderedDict[str, list[Event]] = OrderedDict()
        self._locks: dict[str, asyncio.Lock] = {}
        self._max = max_runs

    async def events(self, run_id: str) -> list[Event]:
        """All events of the run, in order (a copy of the cached list)."""
        lock = self._locks.setdefault(run_id, asyncio.Lock())
        async with lock:
            cached = self._runs.get(run_id)
            if cached is None:
                cached = []
                self._runs[run_id] = cached
            after = cached[-1].id if cached else 0
            async for event in self._bus.replay(run_id, after):
                cached.append(event)
            self._runs.move_to_end(run_id)
            while len(self._runs) > self._max:
                old, _ = self._runs.popitem(last=False)
                self._locks.pop(old, None)
            return list(cached)

    def last_id(self, run_id: str) -> int:
        cached = self._runs.get(run_id)
        return cached[-1].id if cached else 0
