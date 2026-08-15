"""Live router: WebSocket stream of change events."""

from __future__ import annotations

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from ..dependencies import get_live_hub
from ..ws.hub import LiveHub

router = APIRouter(tags=["live"])


@router.websocket("/ws/live")
async def live_stream(websocket: WebSocket, hub: LiveHub = Depends(get_live_hub)) -> None:
    await hub.manager.connect(websocket)
    try:
        while True:
            # We don't expect client messages; this keeps the socket open and
            # detects disconnects promptly.
            await websocket.receive_text()
    except WebSocketDisconnect:
        await hub.manager.disconnect(websocket)
    except Exception:
        await hub.manager.disconnect(websocket)
