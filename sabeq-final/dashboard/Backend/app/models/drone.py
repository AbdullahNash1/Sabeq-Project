from .base import ApiModel
from .common import DroneStateValue, LinkState, Position


class DronePayload(ApiModel):
    """Hardware present and healthy (on/off state lives on the mission)."""

    camera: bool
    speaker: bool
    led: bool


class Detector(ApiModel):
    model: str
    fps: float


class Drone(ApiModel):
    """§3.3 — from /drone/status + /drone/position."""

    id: str
    state: DroneStateValue
    battery_pct: float
    flight_time_left_s: float
    position: Position
    altitude_m: float
    heading_deg: float
    speed_mps: float
    docked_on_cart: bool
    payload: DronePayload
    detector: Detector
    link: LinkState
