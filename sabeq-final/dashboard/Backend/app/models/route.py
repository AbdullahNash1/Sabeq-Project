from .base import ApiModel
from .common import LocalizedText, Position, RouteId


class Route(ApiModel):
    """§3.7 — relayed from ROS /route_scores. The backend adds the label, the projected
    polyline and the score (fastest estTimeS / this route's estTimeS)."""

    id: RouteId
    label: LocalizedText  # {"en": "A · West", "ar": "A · غرب"} (ROUTE_LABELS); the UI shows it, never the id
    length_m: float
    people_count: int
    count_final: bool
    crowd_factor: float | None = None  # Node 2 does not produce one
    est_time_s: float | None = None
    score: float | None = None
    polyline: list[Position]
