"""WS /ws (API_CONTRACT §5): snapshot on connect, broadcasts, ping/pong, 30 s ping timeout."""
import asyncio
import json
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from app.broadcaster import encode
from app.models.ws import CLIENT_MESSAGES, Envelope, Ping, Pong, WsError

router = APIRouter()


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    app = websocket.app
    service, broadcaster = app.state.service, app.state.broadcaster
    timeout = app.state.settings.ws_ping_timeout_s

    await websocket.accept()
    service.system_status()  # refresh uptime
    client = broadcaster.register(websocket, encode("snapshot", service.store.snapshot(service.now())))
    sender = asyncio.create_task(client.run_sender())

    deadline = time.monotonic() + timeout  # only a ping extends it
    try:
        while True:
            remaining = deadline - time.monotonic()
            try:
                message = await asyncio.wait_for(websocket.receive(), timeout=max(remaining, 0))
            except asyncio.TimeoutError:
                await websocket.close(code=1000, reason="ping timeout")
                break
            if message["type"] == "websocket.disconnect":
                break
            if _handle(message.get("text"), client, broadcaster):
                deadline = time.monotonic() + timeout
    except WebSocketDisconnect:
        pass
    finally:
        broadcaster.unregister(client)
        sender.cancel()


def _handle(text, client, broadcaster) -> bool:
    """Process one client message; return True if it was a valid ping."""
    try:
        envelope = Envelope.model_validate(json.loads(text if text is not None else ""))
        if envelope.type not in CLIENT_MESSAGES:
            raise ValueError(f"unknown message type '{envelope.type}'")
        ping = Ping.model_validate(envelope.data)
    except (ValueError, ValidationError) as exc:  # JSONDecodeError is a ValueError
        broadcaster.send_to(client, "error", WsError(code="bad_request", message=_short(exc)))
        return False
    broadcaster.send_to(client, "pong", Pong(client_ts=ping.client_ts))
    return True


def _short(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        first = exc.errors()[0]
        return f"invalid message: {'.'.join(map(str, first['loc']))} {first['msg']}"
    return f"invalid message: {exc}"
