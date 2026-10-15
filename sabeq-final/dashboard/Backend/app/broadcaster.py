"""Push WS messages to every connected client (API_CONTRACT §5).

`publish()` can be called from anywhere — routers, the service, timers, and
later ROS callback threads. Off the event loop it hands over with
call_soon_threadsafe. Each client has its own bounded queue drained by a sender
task, so one slow client never blocks the rest; a client whose queue fills up
is disconnected (it will reconnect and receive a fresh snapshot).
"""
import asyncio
import logging
from datetime import datetime

from fastapi import WebSocket
from pydantic import BaseModel

from app.models.ws import make_envelope

log = logging.getLogger(__name__)


def encode(type_: str, data: BaseModel, ts: "datetime | None" = None) -> str:
    return make_envelope(type_, data, ts).model_dump_json()


class Client:
    def __init__(self, websocket: WebSocket, queue_size: int):
        self.websocket = websocket
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=queue_size)
        self.dropped = False

    def enqueue(self, text: str) -> bool:
        if self.dropped:
            return False
        try:
            self.queue.put_nowait(text)
            return True
        except asyncio.QueueFull:
            self.dropped = True
            return False

    async def run_sender(self) -> None:
        while True:
            text = await self.queue.get()
            await self.websocket.send_text(text)


class Broadcaster:
    def __init__(self, queue_size: int = 256):
        self._queue_size = queue_size
        self._clients: set = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    @property
    def client_count(self) -> int:
        return len(self._clients)

    # --- called by the /ws endpoint (on the loop) ---------------------------

    def register(self, websocket: WebSocket, first_message: str) -> Client:
        """Queue `first_message` (the snapshot) and register in one step, so no
        broadcast can arrive before it."""
        client = Client(websocket, self._queue_size)
        client.enqueue(first_message)
        self._clients.add(client)
        return client

    def unregister(self, client: Client) -> None:
        self._clients.discard(client)

    def send_to(self, client: Client, type_: str, data: BaseModel) -> None:
        """Message for one client only (pong, error replies)."""
        client.enqueue(encode(type_, data))

    # --- called from anywhere ---------------------------------------------------

    def publish(self, type_: str, data: BaseModel) -> None:
        """Broadcast a §5.2 message to all clients; ts = server time now."""
        text = encode(type_, data)
        if self._loop is None:
            return  # app not started: there are no clients
        try:
            on_loop = asyncio.get_running_loop() is self._loop
        except RuntimeError:
            on_loop = False
        if on_loop:
            self._fanout(text)
        else:
            self._loop.call_soon_threadsafe(self._fanout, text)

    def _fanout(self, text: str) -> None:
        for client in list(self._clients):
            if not client.enqueue(text):
                log.warning("WS client too slow, disconnecting")
                self._clients.discard(client)
                asyncio.ensure_future(_close_quietly(client.websocket, 1013, "too slow"))


async def _close_quietly(websocket: WebSocket, code: int, reason: str) -> None:
    try:
        await websocket.close(code=code, reason=reason)
    except Exception:  # already closed
        pass
