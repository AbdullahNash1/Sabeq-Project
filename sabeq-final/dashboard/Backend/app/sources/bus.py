"""CommandBus: the service publishes a command once; every registered sink gets it.

The sinks are the active source of each part (mock or ros) plus, in tests,
a RecordingSink. The service never knows which sources are real.
"""
import logging
from typing import Protocol

from app.sources.commands import Command

log = logging.getLogger(__name__)


class CommandSink(Protocol):
    def handle(self, cmd: Command) -> None: ...


class AckGate(Protocol):
    """Tracks command_ack replies (the ROS sources). register() is called BEFORE the command
    is published and returns an awaitable that resolves when every node that acts on the
    command has accepted it (raising CommandRejected / CommandTimeout otherwise), or None when
    no node has to acknowledge it (mock parts, payload, /incident)."""

    def register(self, cmd: Command): ...


class CommandBus:
    def __init__(self) -> None:
        self._sinks: list = []
        self.ack_gate: "AckGate | None" = None

    def add(self, sink: CommandSink) -> None:
        self._sinks.append(sink)

    def set_ack_gate(self, gate: "AckGate | None") -> None:
        self.ack_gate = gate

    def register_acks(self, cmd: Command):
        """Awaitable for the command's acknowledgements, or None if nobody must ack it."""
        return self.ack_gate.register(cmd) if self.ack_gate is not None else None

    def publish(self, cmd: Command) -> None:
        for sink in self._sinks:
            try:
                sink.handle(cmd)
            except Exception:  # one broken sink must not fail the REST call or the others
                log.exception("command sink %s failed on %s", type(sink).__name__, cmd)
