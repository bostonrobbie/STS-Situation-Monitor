"""WebSocket support for real-time bidirectional communication.

Provides a WebSocket endpoint at /ws that:
- Broadcasts events from the EventBus to all connected clients
- Accepts commands from clients (subscribe to specific event types, ping)
- Tracks connected clients with metadata
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from sts_monitor.event_bus import STSEvent, event_bus

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Manages WebSocket connections and message broadcasting."""

    def __init__(self) -> None:
        self._connections: dict[WebSocket, dict[str, Any]] = {}

    @property
    def active_count(self) -> int:
        return len(self._connections)

    @property
    def connections_info(self) -> list[dict[str, Any]]:
        return [
            {
                "connected_at": meta.get("connected_at", ""),
                "subscriptions": list(meta.get("subscriptions", set())),
                "messages_sent": meta.get("messages_sent", 0),
            }
            for meta in self._connections.values()
        ]

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections[websocket] = {
            "connected_at": datetime.now(UTC).isoformat(),
            "subscriptions": set(),  # empty = all events
            "messages_sent": 0,
        }
        logger.info("WebSocket client connected (%d total)", self.active_count)

    def disconnect(self, websocket: WebSocket) -> None:
        self._connections.pop(websocket, None)
        logger.info("WebSocket client disconnected (%d remaining)", self.active_count)

    async def broadcast(self, event: STSEvent) -> int:
        """Broadcast an event to all connected clients. Returns delivery count."""
        if not self._connections:
            return 0

        message = json.dumps(
            {"type": event.event_type, "data": event.payload, "ts": event.timestamp.isoformat()},
            default=str,
        )

        dead: list[WebSocket] = []
        delivered = 0

        for ws, meta in self._connections.items():
            subs = meta.get("subscriptions", set())
            if subs and event.event_type not in subs:
                continue
            try:
                await ws.send_text(message)
                meta["messages_sent"] = meta.get("messages_sent", 0) + 1
                delivered += 1
            except Exception:
                dead.append(ws)

        for ws in dead:
            self._connections.pop(ws, None)

        return delivered

    async def handle_client_message(self, websocket: WebSocket, data: str) -> None:
        """Handle an incoming message from a WebSocket client."""
        try:
            msg = json.loads(data)
        except json.JSONDecodeError:
            await websocket.send_text(json.dumps({"error": "Invalid JSON"}))
            return

        action = msg.get("action", "")

        if action == "ping":
            await websocket.send_text(json.dumps({"action": "pong", "ts": datetime.now(UTC).isoformat()}))

        elif action == "subscribe":
            event_types = msg.get("event_types", [])
            if isinstance(event_types, list):
                meta = self._connections.get(websocket, {})
                meta["subscriptions"] = set(event_types)
                await websocket.send_text(json.dumps({
                    "action": "subscribed",
                    "event_types": event_types,
                }))

        elif action == "unsubscribe":
            meta = self._connections.get(websocket, {})
            meta["subscriptions"] = set()
            await websocket.send_text(json.dumps({"action": "unsubscribed"}))

        elif action == "status":
            await websocket.send_text(json.dumps({
                "action": "status",
                "active_connections": self.active_count,
                "ts": datetime.now(UTC).isoformat(),
            }))

        else:
            await websocket.send_text(json.dumps({
                "error": f"Unknown action: {action}",
                "available_actions": ["ping", "subscribe", "unsubscribe", "status"],
            }))


# Global singleton
ws_manager = ConnectionManager()


async def websocket_endpoint(websocket: WebSocket) -> None:
    """WebSocket endpoint handler — wire this into your FastAPI app."""
    await ws_manager.connect(websocket)

    # Start event bus listener for this connection
    queue = event_bus.subscribe()
    broadcast_task = asyncio.create_task(_relay_events(queue))

    try:
        while True:
            data = await websocket.receive_text()
            await ws_manager.handle_client_message(websocket, data)
    except WebSocketDisconnect:
        pass
    finally:
        ws_manager.disconnect(websocket)
        event_bus.unsubscribe(queue)
        broadcast_task.cancel()


async def _relay_events(queue: asyncio.Queue) -> None:
    """Relay events from the bus to WebSocket clients."""
    while True:
        event = await queue.get()
        await ws_manager.broadcast(event)
