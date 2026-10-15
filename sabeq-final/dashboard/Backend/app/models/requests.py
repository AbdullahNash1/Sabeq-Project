"""Request bodies for §4.3 / §4.4. Unknown fields are rejected (400)."""
from datetime import datetime
from typing import Literal

from pydantic import model_validator

from .base import RequestModel
from .common import IncidentSource, LocalizedText, RouteId, Severity


class CreateIncidentLocation(RequestModel):
    zone: str
    description: LocalizedText
    lat: float | None = None
    lon: float | None = None
    x: float | None = None
    y: float | None = None

    @model_validator(mode="after")
    def _one_coordinate_pair(self):
        has_gps = self.lat is not None and self.lon is not None
        has_map = self.x is not None and self.y is not None
        if not (has_gps or has_map):
            raise ValueError("location needs either lat/lon or x/y")
        return self


class CreateIncident(RequestModel):
    """POST /incidents"""

    severity: Severity
    type: str
    title: LocalizedText
    location: CreateIncidentLocation
    source: IncidentSource
    reported_at: datetime | None = None


class EmptyRequest(RequestModel):
    """`{}` bodies: launch, dismiss, return."""


class SelectRouteRequest(RequestModel):
    """POST /missions/{id}/route — must be one of mission.routes[].id (checked by the service)."""

    route: RouteId


class PayloadRequest(RequestModel):
    """POST /missions/{id}/payload — at least one of speaker/led."""

    speaker: bool | None = None
    led: bool | None = None

    @model_validator(mode="after")
    def _at_least_one(self):
        if self.speaker is None and self.led is None:
            raise ValueError("at least one of 'speaker' or 'led' is required")
        return self


class AbortRequest(RequestModel):
    """POST /missions/{id}/abort — low_battery is backend-internal only."""

    reason: Literal["operator_abort", "return_to_dock"]


class CloseRequest(RequestModel):
    """POST /missions/{id}/close — outcome required from completed, ignored otherwise."""

    outcome: Literal["resolved", "transferred"] | None = None
