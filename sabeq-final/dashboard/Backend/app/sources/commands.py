"""Typed commands the service publishes on the CommandBus.

Every part's active source receives every command and acts on the ones it
cares about. The ROS wire format lives in app/ros/messages.py, not here.
"""
import uuid
from dataclasses import dataclass, field
from typing import Literal, Optional, Union

from app.models import Position

MissionCommandName = Literal["launch", "select_route", "start_return", "abort", "reset"]  # reset: world_node only


@dataclass(frozen=True)
class MissionCommand:
    """/mission/command (ROS_TOPICS §4.11)."""

    mission_id: str
    command: MissionCommandName
    route: Optional[str] = None
    source: Optional[str] = None  # auto | operator (select_route)
    polyline_local: Optional[list] = None  # [[e, n], ...] world ENU metres: the cart path or return path
    scan_altitude_m: Optional[float] = None
    reason: Optional[str] = None  # operator_abort | return_to_dock | low_battery
    command_id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass(frozen=True)
class PayloadCommand:
    """/drone/payload_cmd (§4.12). None = leave unchanged."""

    mission_id: str
    speaker: Optional[bool] = None
    led: Optional[bool] = None


@dataclass(frozen=True)
class IncidentCommand:
    """/incident (§4.15). active=False clears it (mission closed or dismissed)."""

    active: bool
    mission_id: Optional[str] = None
    incident_id: Optional[str] = None
    severity: Optional[str] = None
    type: Optional[str] = None
    zone: Optional[str] = None
    position: Optional[Position] = None  # the world incident, projected
    local_m: Optional[tuple] = None  # (e, n): always the world incident
    scan_altitude_m: Optional[float] = None


Command = Union[MissionCommand, PayloadCommand, IncidentCommand]


# --- command acknowledgements (ROS_TOPICS §4.11) ---------------------------------------------

class CommandFailed(Exception):
    """A published command was not accepted by every node that acts on it."""

    def __init__(self, command: str, message: str):
        super().__init__(message)
        self.command = command
        self.message = message


class CommandRejected(CommandFailed):
    """A node answered command_ack with accepted: false."""

    def __init__(self, command: str, node: str, reason: "str | None"):
        super().__init__(command, f"{node} rejected {command}: {reason or 'no reason given'}")
        self.node = node
        self.reason = reason


class CommandTimeout(CommandFailed):
    """No command_ack from some node within the ack timeout."""

    def __init__(self, command: str, nodes: list, timeout_s: float):
        super().__init__(command, f"no acknowledgement of {command} from {', '.join(nodes)} "
                                  f"within {timeout_s:g} s")
        self.nodes = list(nodes)
        self.timeout_s = timeout_s
