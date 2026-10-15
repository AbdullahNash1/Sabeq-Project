from typing import Annotated, Literal

from pydantic import StringConstraints, model_validator

from .base import ApiModel

# Route ids are a dynamic list: the route names in routes.yaml (A_west, B_north, C_east).
# Never enumerate them.
RouteId = Annotated[str, StringConstraints(min_length=1)]

Severity = Literal["critical", "high", "medium", "low"]
IncidentStatus = Literal[
    "queued", "active", "resolved", "transferred", "cancelled", "aborted", "error"
]
IncidentSource = Literal["staff_radio", "dispatch", "sensor", "operator"]
DroneStateValue = Literal[
    "docked", "taking_off", "scanning", "escorting", "returning_to_dock", "landing", "fault", "offline"
]
LinkState = Literal["ok", "degraded", "lost"]
CartStateValue = Literal["at_hub", "to_incident", "at_incident", "to_hub", "stopped"]
ScanPhase = Literal["pending", "climb", "rotate", "done"]
EscortLeg = Literal["outbound", "return"]
RescueState = Literal["in_progress", "patient_loaded"]
SelectionSource = Literal["auto", "operator"]
Outcome = Literal["resolved", "transferred", "cancelled", "aborted", "error"]
AbortReason = Literal["operator_abort", "return_to_dock", "low_battery"]
CameraProtocol = Literal["mjpeg"]
CameraMode = Literal["off", "scan_360", "forward"]


class LocalizedText(ApiModel):
    en: str | None = None
    ar: str | None = None

    @model_validator(mode="after")
    def _at_least_one(self):
        if self.en is None and self.ar is None:
            raise ValueError("at least one of 'en' or 'ar' is required")
        return self


class Position(ApiModel):
    """GPS + SVG map coordinates (§1.2). Both pairs are always present."""

    lat: float
    lon: float
    x: float
    y: float
