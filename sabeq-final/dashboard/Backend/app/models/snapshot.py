from datetime import datetime

from .base import ApiModel
from .camera import CameraInfo
from .cart import Cart
from .drone import Drone
from .incident import Incident
from .mission import Mission, MissionSummary
from .site import Site
from .system import SystemStatus


class OpenIncidents(ApiModel):
    active: int
    queued: int


class Snapshot(ApiModel):
    """GET /api/snapshot and the WS `snapshot` message (§4.1)."""

    site: Site
    system: SystemStatus
    drones: list[Drone]
    carts: list[Cart]
    active_mission: Mission | None = None
    active_incident: Incident | None = None
    open_incidents: OpenIncidents
    recent_missions: list[MissionSummary]
    camera: CameraInfo
    server_time: datetime
