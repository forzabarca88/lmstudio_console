"""SSE log streamer — ring buffer + subscriber broadcast for live trace logs."""

import asyncio
import itertools
import json
from collections import deque
from typing import AsyncIterable, Dict, List, Tuple


class LogStreamer:
    """Manages a ring buffer of recent log entries and broadcasts them to SSE subscribers."""

    def __init__(self, max_size: int = 500):
        self._buffer: deque = deque(maxlen=max_size)
        self._subscribers: Dict[int, asyncio.Queue] = {}
        self._lock = asyncio.Lock()
        self._id_counter = itertools.count()

    def push(self, entry: dict) -> None:
        """Add an entry to the buffer and broadcast to all subscribers (non-blocking)."""
        self._buffer.append(entry)
        sse_line = f"data: {json.dumps(entry)}\n\n"
        for queue in self._subscribers.values():
            try:
                queue.put_nowait(sse_line)
            except asyncio.QueueFull:
                pass

    async def subscribe(self) -> Tuple[int, AsyncIterable[str]]:
        """Create a subscriber.

        Returns a tuple of (subscriber_id, async_iterable) where the caller
        is responsible for calling ``remove(subscriber_id)`` when done.
        """
        sub_id = next(self._id_counter)
        queue: asyncio.Queue = asyncio.Queue(maxsize=200)
        async with self._lock:
            self._subscribers[sub_id] = queue

        async def _iter() -> AsyncIterable[str]:
            while True:
                yield await queue.get()

        return sub_id, _iter()

    async def remove(self, sub_id: int) -> None:
        """Remove a subscriber."""
        async with self._lock:
            self._subscribers.pop(sub_id, None)

    def get_recent(self, count: int) -> List[dict]:
        """Return the last N entries from the buffer."""
        return list(self._buffer)[-count:]


# Module-level singleton
log_streamer = LogStreamer()
