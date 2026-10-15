"""RosContext: what the four ROS sources share (one per app).

- one Transport (the "backend_bridge" node), started by the first ROS source;
- one /mission_events subscription, dispatched by `source` to the drone source (mission_node)
  and the cart source (world_node); `command_ack` events go to the AckGate;
- one command publisher: every ROS part receives each command from the CommandBus, the
  context publishes it once (/mission/command, /drone/payload_cmd, /incident);
- the AckGate the service waits on (ROS_TOPICS §4.11): which nodes must acknowledge which
  command, given which parts are ROS.
"""
import asyncio
from collections import deque
import logging
from typing import Callable

from app.ros import topics as T
from app.ros.messages import to_ros
from app.ros.transport import RclpyTransport, Transport
from app.ros.wire import LATCHED_1, RELIABLE_20
from app.sources.commands import (
    CommandRejected,
    CommandTimeout,
    IncidentCommand,
    MissionCommand,
    PayloadCommand,
)

log = logging.getLogger(__name__)

# ROS_TOPICS §4.11: who acts on (and therefore acknowledges) each /mission/command
MISSION_NODE_COMMANDS = ("launch", "select_route", "start_return", "abort")
WORLD_NODE_COMMANDS = ("launch", "select_route", "start_return", "abort", "reset")


class RosContext:

    def __init__(self, settings, service, transport: "Transport | None" = None):
        self.settings = settings
        self.service = service
        self.transport = transport or RclpyTransport(
            settings.ros_node_name, use_sim_time=settings.ros_use_sim_time)
        self.ros_parts: set = set()      # parts whose source is ROS
        self._started = False
        self._starts = 0
        self._event_handlers: dict = {}  # event source node -> callback(event dict)
        self._recent: deque = deque(maxlen=64)  # ids of commands already published
        self._pending: dict = {}         # commandId -> _PendingAcks
        self._publishers: dict = {}

    # --- lifecycle -------------------------------------------------------------------------
    def register_part(self, part: str) -> None:
        self.ros_parts.add(part)

    async def start(self) -> None:
        self._starts += 1
        if self._started:
            return
        self.transport.start(asyncio.get_running_loop())
        self._started = True
        self.transport.subscribe(T.MISSION_EVENTS, "json", RELIABLE_20, self._on_event)
        self.service.bus.set_ack_gate(self)

    async def stop(self) -> None:
        self._starts -= 1
        if self._started and self._starts <= 0:
            self._started = False
            self.service.bus.set_ack_gate(None)
            for pending in list(self._pending.values()):
                pending.cancel()
            self.transport.stop()

    @property
    def started(self) -> bool:
        return self._started

    def now(self) -> float:
        return self.transport.now()

    def subscribe(self, topic: str, kind: str, qos, callback: Callable) -> None:
        self.transport.subscribe(topic, kind, qos, callback)

    def on_events_from(self, node: str, callback: Callable) -> None:
        self._event_handlers[node] = callback

    # --- /mission_events ---------------------------------------------------------------------
    def _on_event(self, event: dict) -> None:
        name = event.get("event")
        if name == "command_ack":
            self._on_ack(event.get("source"), event.get("details") or {})
            return
        handler = self._event_handlers.get(event.get("source"))
        if handler is not None:
            handler(event)

    # --- commands --------------------------------------------------------------------------------
    def publish_command(self, cmd) -> None:
        """Publish a CommandBus command once, whichever ROS part received it."""
        if not self._started:
            return
        key = id(cmd)
        if key in self._recent:
            return
        self._recent.append(key)
        topic, message = to_ros(cmd)
        if "simTimeS" in message:
            message["simTimeS"] = round(self.now(), 3)
        qos = LATCHED_1 if isinstance(cmd, IncidentCommand) else RELIABLE_20
        if topic not in self._publishers:
            self._publishers[topic] = self.transport.publisher(topic, "json", qos)
        self._publishers[topic](message)

    # --- AckGate (app/sources/bus.py) ----------------------------------------------------------
    def expected_acks(self, cmd) -> list:
        if not isinstance(cmd, MissionCommand) or not self._started:
            return []
        nodes = []
        if "drone" in self.ros_parts and cmd.command in MISSION_NODE_COMMANDS:
            nodes.append("mission_node")
        if "cart" in self.ros_parts and cmd.command in WORLD_NODE_COMMANDS:
            nodes.append("world_node")
        return nodes

    def register(self, cmd):
        """Called by the service BEFORE publishing: an awaitable for the acks, or None."""
        nodes = self.expected_acks(cmd)
        if not nodes:
            return None
        pending = _PendingAcks(cmd, nodes, self.settings.command_ack_timeout_s,
                               lambda: self._pending.pop(cmd.command_id, None))
        self._pending[cmd.command_id] = pending
        return pending.wait()

    def _on_ack(self, node: "str | None", details: dict) -> None:
        pending = self._pending.get(details.get("commandId"))
        if pending is not None and node is not None:
            pending.ack(node, bool(details.get("accepted")), details.get("reason"))


class _PendingAcks:

    def __init__(self, cmd: MissionCommand, nodes: list, timeout_s: float, done: Callable):
        self.cmd = cmd
        self.waiting = list(nodes)
        self.timeout_s = timeout_s
        self.loop = asyncio.get_running_loop()
        self.future = self.loop.create_future()
        self._done = done
        self._timer = self.loop.call_later(timeout_s, self._timeout)

    def ack(self, node: str, accepted: bool, reason) -> None:
        if self.future.done() or node not in self.waiting:
            return
        if not accepted:
            self._finish(CommandRejected(self.cmd.command, node, reason))
            return
        self.waiting.remove(node)
        if not self.waiting:
            self._finish(None)

    def _timeout(self) -> None:
        if not self.future.done():
            self._finish(CommandTimeout(self.cmd.command, self.waiting, self.timeout_s))

    def _finish(self, error) -> None:
        self._timer.cancel()
        self._done()
        if error is None:
            self.future.set_result(None)
        else:
            self.future.set_exception(error)

    def cancel(self) -> None:
        self._timer.cancel()
        if not self.future.done():
            self.future.cancel()

    async def wait(self) -> None:
        await self.future
