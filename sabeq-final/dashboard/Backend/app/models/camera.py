from datetime import datetime

from .base import ApiModel
from .common import CameraMode, CameraProtocol, RouteId


class ImageSize(ApiModel):
    w: int
    h: int


class CameraInfo(ApiModel):
    """§3.9 — MJPEG from raw /camera/image."""

    drone_id: str
    available: bool
    stream_url: str
    protocol: CameraProtocol
    mode: CameraMode
    resolution: ImageSize


class Box(ApiModel):
    """One detection in percent of the frame (0–100); x/y = top-left. No route field."""

    x: float
    y: float
    w: float
    h: float
    conf: float
    cls: str


class CameraDetections(ApiModel):
    """WS `camera.detections` data (§5.2)."""

    drone_id: str
    mission_id: str | None = None
    mode: CameraMode
    heading_deg: float | None = None
    frame_stamp: datetime  # backend receive time, never a ROS sim stamp
    sim_time_s: float | None = None
    image_size: ImageSize
    boxes: list[Box]
    counts: dict[RouteId, int] | None = None
