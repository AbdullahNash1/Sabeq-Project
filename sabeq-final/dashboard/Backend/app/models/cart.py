from .base import ApiModel
from .common import CartStateValue, Position


class Cart(ApiModel):
    """§3.4 — v1 position from Gazebo /cart/pose (v2: operator tablet GPS)."""

    id: str
    position: Position
    heading_deg: float
    speed_kmh: float
    state: CartStateValue
