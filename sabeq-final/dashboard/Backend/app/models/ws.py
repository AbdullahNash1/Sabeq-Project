"""WebSocket envelope and one data model per message type (§5.2, §5.3)."""
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from app.state.machine import MissionState

from .base import ApiModel
from .camera import CameraDetections, CameraInfo
from .common import (
    CartStateValue,
    DroneStateValue,
    EscortLeg,
    LinkState,
    Position,
    RouteId,
    ScanPhase,
    SelectionSource,
)
from .incident import Incident
from .mission import Mission, MissionSummary
from .route import Route
from .snapshot import OpenIncidents, Snapshot
from .system import SystemStatus


class Envelope(ApiModel):
    """{"type": "...", "data": {...}, "ts": "ISO time"} — ts is backend send time."""

    type: str
    data: dict[str, Any]
    ts: datetime


# --- server -> client ------------------------------------------------------


class IncidentEvent(ApiModel):
    """incident.created / incident.updated"""

    incident: Incident
    open_incidents: OpenIncidents


class MissionStateEvent(ApiModel):
    mission_id: str
    from_: MissionState = Field(alias="from")
    to: MissionState
    reason: str | None = None
    mission: Mission


class DroneTelemetry(ApiModel):
    drone_id: str
    state: DroneStateValue
    battery_pct: float
    flight_time_left_s: float
    position: Position
    altitude_m: float
    heading_deg: float
    speed_mps: float
    docked_on_cart: bool
    link: LinkState


class CartTelemetry(ApiModel):
    cart_id: str
    state: CartStateValue
    position: Position
    heading_deg: float
    speed_kmh: float


class ScanProgress(ApiModel):
    mission_id: str
    phase: ScanPhase
    progress: float
    altitude_m: float
    heading_deg: float
    coverage_pct: float


class RouteScores(ApiModel):
    mission_id: str
    routes: list[Route]
    final: bool


class RouteRecommendation(ApiModel):
    mission_id: str
    recommended: RouteId
    routes: list[Route]
    auto_select_at: datetime
    countdown_s: float


class RouteSelected(ApiModel):
    mission_id: str
    route: RouteId
    source: SelectionSource


class EscortProgress(ApiModel):
    mission_id: str
    leg: EscortLeg
    progress: float
    elapsed_s: float
    eta_s: float | None = None
    drone_ahead_of_cart_m: float | None = None


class PayloadStateEvent(ApiModel):
    mission_id: str | None = None
    speaker: bool
    led: bool


class MissionHistoryEvent(ApiModel):
    item: MissionSummary


class WsError(ApiModel):
    code: str
    message: str
    mission_id: str | None = None


class Pong(ApiModel):
    client_ts: str  # echoed verbatim


# --- client -> server ------------------------------------------------------


class Ping(ApiModel):
    client_ts: str


SERVER_MESSAGES: dict = {
    "snapshot": Snapshot,
    "system.status": SystemStatus,
    "incident.created": IncidentEvent,
    "incident.updated": IncidentEvent,
    "mission.state": MissionStateEvent,
    "drone.telemetry": DroneTelemetry,
    "cart.telemetry": CartTelemetry,
    "scan.progress": ScanProgress,
    "route.scores": RouteScores,
    "route.recommendation": RouteRecommendation,
    "route.selected": RouteSelected,
    "escort.progress": EscortProgress,
    "payload.state": PayloadStateEvent,
    "camera.detections": CameraDetections,
    "camera.info": CameraInfo,
    "mission.history": MissionHistoryEvent,
    "error": WsError,
    "pong": Pong,
}

CLIENT_MESSAGES: dict = {"ping": Ping}


def make_envelope(type_: str, data: BaseModel, ts: "datetime | None" = None) -> Envelope:
    """Wrap a server message; rejects types/models not in the contract."""
    expected = SERVER_MESSAGES.get(type_)
    if expected is None:
        raise ValueError(f"unknown WS message type '{type_}'")
    if not isinstance(data, expected):
        raise TypeError(f"'{type_}' expects {expected.__name__}, got {type(data).__name__}")
    return Envelope(
        type=type_,
        data=data.model_dump(mode="json"),
        ts=ts or datetime.now(timezone.utc),
    )
