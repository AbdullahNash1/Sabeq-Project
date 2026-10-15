"""One interface per part. Sources feed the service; the service never knows which is real.

Rules for every source:
- Change state ONLY through MissionService hooks; the store is read-only for sources.
- `handle(cmd)` must only record intent and return. Act on the next tick, never call
  service hooks from inside handle() (the service is mid-transition when it publishes).
- Everything runs on the event loop. A ROS source must hand work over with
  loop.call_soon_threadsafe from its rclpy callback threads.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, AsyncIterator

from app.config import Settings
from app.sources.commands import Command

if TYPE_CHECKING:
    from app.service import MissionService
    from app.state.store import Store


class Source(ABC):
    part: str = ""

    def __init__(self, service: "MissionService", store: "Store", settings: Settings):
        self.service = service
        self.store = store
        self.settings = settings

    def handle(self, cmd: Command) -> None:
        """Command sink. Default: ignore."""

    async def start(self) -> None:
        """Called once the event loop runs (app startup)."""

    async def stop(self) -> None:
        """Called on app shutdown."""

    def reset(self) -> None:
        """Back to boot state (POST /api/dev/sim/reset)."""


class CameraSource(Source):
    """Camera stream + person detections -> camera_status(), camera_detections()."""

    part = "camera"

    @abstractmethod
    def frames(self) -> AsyncIterator[bytes]:
        """JPEG frames for the MJPEG stream, paced at <= settings.mjpeg_max_fps."""


class RouteSource(Source):
    """Node 2: route scores + selected route -> route_scores(), route_recommended().

    Everything specific to Node 2's message format stays inside the route source."""

    part = "route"


class DroneSource(Source):
    """mission_node: drone status/position + mission events -> drone_telemetry(), scan_progress(),
    scan_altitude_reached(), payload_confirmed(), drone_landed(), battery_low(), fault()."""

    part = "drone"


class CartSource(Source):
    """world_node: cart pose + status -> cart_telemetry(), cart_at_incident(), patient_loaded(),
    cart_at_hub()."""

    part = "cart"


@dataclass
class SourceSet:
    camera: CameraSource
    route: RouteSource
    drone: DroneSource
    cart: CartSource
    ros: object = None   # RosContext when any part is ROS (app/ros/context.py)

    def all(self) -> list:
        return [self.camera, self.route, self.drone, self.cart]

    async def start(self) -> None:
        for source in self.all():
            await source.start()

    async def stop(self) -> None:
        for source in self.all():
            await source.stop()

    def reset(self) -> None:
        for source in self.all():
            source.reset()
