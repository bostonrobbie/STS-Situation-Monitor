"""In-process event bus for real-time SSE broadcasting.

Publishes events from ingestion, pipeline runs, alerts, and geo_events
to connected SSE subscribers.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass(slots=True)
class STSEvent:
    """A typed event emitted to SSE subscribers."""

    event_type: str  # observation, geo_event, alert, convergence, pipeline_run, ingestion
    payload: dict[str, Any]
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_sse(self) -> str:
        data = json.dumps(
            {"type": self.event_type, "data": self.payload, "ts": self.timestamp.isoformat()},
            default=str,
        )
        return f"event: {self.event_type}\ndata: {data}\n\n"


class EventBus:
    """Simple async pub/sub for broadcasting events to SSE subscribers."""

    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue[STSEvent]] = []

    def subscribe(self, max_size: int = 256) -> asyncio.Queue[STSEvent]:
        queue: asyncio.Queue[STSEvent] = asyncio.Queue(maxsize=max_size)
        self._subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[STSEvent]) -> None:
        self._subscribers = [q for q in self._subscribers if q is not queue]

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    async def publish(self, event: STSEvent) -> int:
        """Publish event to all subscribers. Returns number delivered."""
        dead: list[asyncio.Queue[STSEvent]] = []
        delivered = 0
        for queue in self._subscribers:
            try:
                queue.put_nowait(event)
                delivered += 1
            except asyncio.QueueFull:
                dead.append(queue)
        for q in dead:
            self._subscribers.remove(q)
        return delivered

    def publish_sync(self, event: STSEvent) -> None:
        """Fire-and-forget publish from sync code (schedules onto event loop)."""
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.publish(event))
        except RuntimeError:
            pass  # No event loop running; skip SSE delivery


# Global singleton
event_bus = EventBus()
