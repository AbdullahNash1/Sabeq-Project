from datetime import datetime

from pydantic import model_validator

from app.state.machine import MissionState

from .base import ApiModel
from .common import (
    AbortReason, EscortLeg, LocalizedText, Outcome, Position, RescueState, RouteId, ScanPhase, SelectionSource,
)
from .incident import IncidentType
from .route import Route


class ScanInfo(ApiModel):
    phase: ScanPhase = "pending"
    progress: float = 0
    coverage_pct: float = 0


class EscortInfo(ApiModel):
    leg: EscortLeg | None = None
    progress: float = 0
    elapsed_s: float = 0
    eta_s: float | None = None
    drone_ahead_of_cart_m: float | None = None


class PayloadState(ApiModel):
    """Speaker/LED on-off for the mission. Also the response of POST /payload."""

    speaker: bool
    led: bool


class RescueInfo(ApiModel):
    """Medics at the incident: set on arrived, never goes back (§3.6)."""

    state: RescueState
    patient_loaded_at: datetime | None = None


class TimelineEntry(ApiModel):
    state: MissionState
    at: datetime


class MissionResult(ApiModel):
    response_time_s: float
    vs_best_alternative_s: float | None = None
    people_warned: int
    drone_battery_pct: float


class MissionError(ApiModel):
    code: str
    message: str


class Mission(ApiModel):
    """§3.6"""

    id: str
    incident_id: str
    drone_id: str
    cart_id: str
    state: MissionState
    state_entered_at: datetime
    scan_altitude_m: float
    scan: ScanInfo
    routes: list[Route]
    recommended_route: RouteId | None = None
    preselected_route: RouteId | None = None  # operator's choice made during the scan
    selected_route: RouteId | None = None
    selection_source: SelectionSource | None = None
    auto_select_at: datetime | None = None
    escort: EscortInfo
    # Return leg of the selected route (route reversed -> start -> First Aid Center), set on lock
    return_polyline: list[Position] | None = None
    payload: PayloadState
    rescue: RescueInfo | None = None
    timeline: list[TimelineEntry]
    result: MissionResult | None = None
    outcome: Outcome | None = None
    abort_reason: AbortReason | None = None
    error: MissionError | None = None
    created_at: datetime
    ended_at: datetime | None = None

    @model_validator(mode="after")
    def _route_ids_from_list(self):
        # Route ids are dynamic: a referenced id must exist in this mission's list.
        if self.routes:
            ids = {r.id for r in self.routes}
            for field in ("recommended_route", "preselected_route", "selected_route"):
                value = getattr(self, field)
                if value is not None and value not in ids:
                    raise ValueError(f"{field} '{value}' is not one of {sorted(ids)}")
        return self


class MissionSummary(ApiModel):
    """§3.8 — history item ("Recent" list); last 10 kept in memory."""

    id: str
    reported_at: datetime
    incident_type: IncidentType
    zone: str
    route: RouteId | None = None
    route_label: LocalizedText | None = None  # the route's label at close ({"en": "C · East", "ar": "C · شرق"})
    response_time_s: float | None = None
    drone_id: str | None = None
    outcome: Outcome
