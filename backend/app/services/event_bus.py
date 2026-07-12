"""In-process pub/sub used to fan out live updates to WebSocket clients.

Replaces the vision doc's MQTT bus for the MVP: topics are plain strings,
payloads are JSON-serializable dicts. Subscribers get an asyncio.Queue; slow
subscribers drop oldest events rather than blocking publishers.
"""

from __future__ import annotations

import asyncio
from typing import Any


class EventBus:
    def __init__(self, max_queue: int = 500) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        self._max_queue = max_queue

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=self._max_queue)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def publish(self, topic: str, payload: Any) -> None:
        event = {"topic": topic, "payload": payload}
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                try:
                    q.get_nowait()  # drop oldest
                    q.put_nowait(event)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass


bus = EventBus()
