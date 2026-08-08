"""Real-time WebSocket hub (Observer / pub-sub).

A single background Kafka consumer tails the CDC topic and fans each change out
to all connected browsers. Clients are pure observers; they never talk to Kafka
directly. This mirrors the server-authoritative WebSocket fan-out pattern from
the CricLot project.
"""

from __future__ import annotations

import asyncio
import json
import threading
from typing import Any, Optional

from confluent_kafka import Consumer, KafkaError
from fastapi import WebSocket

from pulsesearch_common.config import KafkaSettings
from pulsesearch_common.logging import configure_logging
from pulsesearch_common.metrics import WS_CONNECTIONS

log = configure_logging("api.ws")


class ConnectionManager:
    """Tracks active WebSocket connections and broadcasts to them."""

    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections.add(websocket)
        WS_CONNECTIONS.set(len(self._connections))

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._connections.discard(websocket)
        WS_CONNECTIONS.set(len(self._connections))

    async def broadcast(self, message: dict[str, Any]) -> None:
        payload = json.dumps(message, default=str)
        async with self._lock:
            targets = list(self._connections)
        stale: list[WebSocket] = []
        for ws in targets:
            try:
                await ws.send_text(payload)
            except Exception:  # noqa: BLE001 - client vanished mid-send
                stale.append(ws)
        if stale:
            async with self._lock:
                for ws in stale:
                    self._connections.discard(ws)
            WS_CONNECTIONS.set(len(self._connections))


class LiveHub:
    """Bridges a background Kafka consumer to the async ConnectionManager."""

    def __init__(self, settings: KafkaSettings) -> None:
        self._settings = settings
        self.manager = ConnectionManager()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._running = True
        self._thread = threading.Thread(target=self._consume, name="ws-hub", daemon=True)
        self._thread.start()
        log.info("live hub started")

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=5.0)
        log.info("live hub stopped")

    # -- background consumer ---------------------------------------------
    def _consume(self) -> None:
        consumer = Consumer(
            {
                "bootstrap.servers": self._settings.bootstrap_servers,
                # Ephemeral, unique group so the UI always sees the live tail
                # without competing with the sync worker for partitions.
                "group.id": f"pulsesearch-ws-{id(self)}",
                "auto.offset.reset": "latest",
                "enable.auto.commit": False,
            }
        )
        consumer.subscribe([self._settings.source_topic])
        try:
            while self._running:
                msg = consumer.poll(timeout=1.0)
                if msg is None or msg.error():
                    if msg and msg.error() and msg.error().code() != KafkaError._PARTITION_EOF:
                        log.warning("ws consumer error", extra={"error": str(msg.error())})
                    continue
                event = self._to_live_event(msg.value())
                if event is not None:
                    self._dispatch(event)
        finally:
            consumer.close()

    def _to_live_event(self, raw: Optional[bytes]) -> Optional[dict[str, Any]]:
        if raw is None:
            return None
        try:
            envelope = json.loads(raw)
            payload = envelope.get("payload", envelope)
            row = payload.get("after") or payload.get("before") or {}
            return {
                "op": payload.get("op"),
                "id": str(row.get("id")),
                "wiki": row.get("wiki"),
                "title": row.get("title"),
                "title_url": row.get("title_url"),
                "last_user": row.get("last_user"),
                "event_type": row.get("event_type"),
                "edit_count": row.get("edit_count"),
                "ts_ms": payload.get("ts_ms"),
            }
        except Exception:  # noqa: BLE001
            return None

    def _dispatch(self, event: dict[str, Any]) -> None:
        if self._loop is None:
            return
        asyncio.run_coroutine_threadsafe(self.manager.broadcast(event), self._loop)
