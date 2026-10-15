from datetime import datetime

from .base import ApiModel
from .common import IncidentSource, IncidentStatus, LocalizedText, Position, Severity


class IncidentType(ApiModel):
    code: str
    label: LocalizedText


class IncidentLocation(ApiModel):
    zone: str
    description: LocalizedText
    position: Position


class Incident(ApiModel):
    """§3.5"""

    id: str
    status: IncidentStatus
    severity: Severity
    type: IncidentType
    location: IncidentLocation
    source: IncidentSource
    reported_at: datetime
    distance_from_hub_m: float
    mission_id: str | None = None
