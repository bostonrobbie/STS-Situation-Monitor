"""Tests for the event bus module."""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import pytest

from sts_monitor.event_bus import EventBus, STSEvent

pytestmark = pytest.mark.unit


# ── STSEvent ───────────────────────────────────────────────────────────


def test_sts_event_to_sse_format() -> None:
    event = STSEvent(
        event_type="observation",
        payload={"claim": "test claim", "source": "rss:example"},
        timestamp=datetime(2025, 1, 15, 12, 0, 0, tzinfo=UTC),
    )
    sse = event.to_sse()
    assert sse.startswith("event: observation\n")
    assert "data: " in sse
    assert sse.endswith("\n\n")

    # Parse the data line
    data_line = sse.split("data: ", 1)[1].strip()
    parsed = json.loads(data_line)
    assert parsed["type"] == "observation"
    assert parsed["data"]["claim"] == "test claim"
    assert "ts" in parsed


def test_sts_event_to_sse_json_serializable() -> None:
    event = STSEvent(
        event_type="geo_event",
        payload={"lat": 51.5, "lon": -0.12, "magnitude": 4.5},
    )
    sse = event.to_sse()
    data_line = sse.split("data: ", 1)[1].strip()
    parsed = json.loads(data_line)
    assert parsed["data"]["lat"] == 51.5


def test_sts_event_default_timestamp() -> None:
    event = STSEvent(event_type="alert", payload={})
    assert event.timestamp is not None
    assert event.timestamp.tzinfo == UTC


# ── EventBus subscribe/unsubscribe ─────────────────────────────────────


def test_subscribe_returns_queue() -> None:
    bus = EventBus()
    queue = bus.subscribe()
    assert isinstance(queue, asyncio.Queue)
    assert bus.subscriber_count == 1


def test_unsubscribe_removes_queue() -> None:
    bus = EventBus()
    q1 = bus.subscribe()
    q2 = bus.subscribe()
    assert bus.subscriber_count == 2
    bus.unsubscribe(q1)
    assert bus.subscriber_count == 1
    bus.unsubscribe(q2)
    assert bus.subscriber_count == 0


def test_unsubscribe_nonexistent_queue_noop() -> None:
    bus = EventBus()
    q = bus.subscribe()
    other_q = asyncio.Queue()
    bus.unsubscribe(other_q)  # Should not raise
    assert bus.subscriber_count == 1


def test_subscriber_count_property() -> None:
    bus = EventBus()
    assert bus.subscriber_count == 0
    q1 = bus.subscribe()
    assert bus.subscriber_count == 1
    q2 = bus.subscribe()
    assert bus.subscriber_count == 2


# ── EventBus publish ───────────────────────────────────────────────────


def test_publish_delivers_to_all_subscribers() -> None:
    import asyncio
    bus = EventBus()
    q1 = bus.subscribe()
    q2 = bus.subscribe()

    event = STSEvent(event_type="test", payload={"msg": "hello"})
    delivered = asyncio.get_event_loop().run_until_complete(bus.publish(event))

    assert delivered == 2
    assert not q1.empty()
    assert not q2.empty()

    e1 = q1.get_nowait()
    e2 = q2.get_nowait()
    assert e1.event_type == "test"
    assert e2.payload["msg"] == "hello"


def test_publish_with_no_subscribers_returns_zero() -> None:
    import asyncio
    bus = EventBus()
    event = STSEvent(event_type="test", payload={})
    delivered = asyncio.get_event_loop().run_until_complete(bus.publish(event))
    assert delivered == 0


# ── publish_sync ───────────────────────────────────────────────────────


def test_publish_sync_no_event_loop_does_not_raise() -> None:
    bus = EventBus()
    bus.subscribe()
    event = STSEvent(event_type="sync_test", payload={"x": 1})
    # No running event loop, should silently pass
    bus.publish_sync(event)
