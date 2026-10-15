"""Single in-memory store (no database, §0). Plain data + small helpers.

The store does not run the state machine or side effects; app/service.py does.
Only mutate it on the asyncio event loop thread (handlers are async; other
threads must go through loop.call_soon_threadsafe).
"""
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.config import Settings
from app.geo import Projection
from app.models import (
    BackendStatus,
    CameraInfo,
    Cart,
    Detector,
    Drone,
    DronePayload,
    ImageSize,
    Incident,
    JetsonStatus,
    LocalizedText,
    Mission,
    MissionSummary,
    OpenIncidents,
    Position,
    RosStatus,
    Site,
    SiteMap,
    Snapshot,
    SystemStatus,
    ViewBox,
)
from app.state.machine import MissionState
from app.world import World, compass_deg


@dataclass
class ClosedRecord:
    mission: Mission
    summary: MissionSummary
    incident: Incident


@dataclass
class MissionRuntime:
    """Backend-internal data for the active mission. Never serialised to the API."""

    auto_select_handle: Any = None  # asyncio.TimerHandle for the 3 s countdown


class Store:
    def __init__(
        self,
        site: Site,
        system: SystemStatus,
        drone: Drone,
        cart: Cart,
        camera: CameraInfo,
        history_limit: int = 10,
    ):
        self.site = site
        self.system = system
        self.drone = drone
        self.cart = cart
        self.camera = camera
        self.active_incident: Incident | None = None
        self.active_mission: Mission | None = None
        self.runtime: MissionRuntime | None = None
        self.queued_incidents: list[Incident] = []  # arrival order (§7)
        self.closed: deque = deque(maxlen=history_limit)  # ClosedRecord, newest first
        self.started_at = datetime.now(timezone.utc)
        self._incident_seq = 0
        self._mission_seq = 0

    # --- state ------------------------------------------------------------

    @property
    def state(self) -> MissionState:
        """System state: the active mission's state, or idle."""
        return self.active_mission.state if self.active_mission else MissionState.IDLE

    # --- ids --------------------------------------------------------------

    def next_incident_id(self) -> str:
        self._incident_seq += 1
        return f"INC-{self._incident_seq:04d}"

    def next_mission_id(self) -> str:
        self._mission_seq += 1
        return f"MSN-{self._mission_seq:04d}"

    # --- lookups ------------------------------------------------------------

    def find_mission(self, mission_id: str) -> Mission | None:
        if self.active_mission and self.active_mission.id == mission_id:
            return self.active_mission
        return next((r.mission for r in self.closed if r.mission.id == mission_id), None)

    def all_incidents(self) -> list[Incident]:
        """Active, then queued (arrival order), then closed (newest first)."""
        active = [self.active_incident] if self.active_incident else []
        return active + list(self.queued_incidents) + [r.incident for r in self.closed]

    def find_incident(self, incident_id: str) -> Incident | None:
        return next((i for i in self.all_incidents() if i.id == incident_id), None)

    # --- incidents queue ----------------------------------------------------

    def enqueue_incident(self, incident: Incident) -> None:
        self.queued_incidents.append(incident)

    def pop_next_queued(self) -> Incident | None:
        return self.queued_incidents.pop(0) if self.queued_incidents else None

    def open_incidents(self) -> OpenIncidents:
        return OpenIncidents(
            active=1 if self.active_incident else 0,
            queued=len(self.queued_incidents),
        )

    # --- history ------------------------------------------------------------

    def reset_from(self, fresh: "Store") -> None:
        """Replace all state with `fresh` in place (dev sim reset); references stay valid."""
        self.__dict__.update(fresh.__dict__)

    def add_closed(self, mission: Mission, summary: MissionSummary, incident: Incident) -> None:
        self.closed.appendleft(ClosedRecord(mission, summary, incident))

    def recent_missions(self) -> list[MissionSummary]:
        return [r.summary for r in self.closed]

    # --- snapshot -----------------------------------------------------------

    def snapshot(self, now: datetime | None = None) -> Snapshot:
        return Snapshot(
            site=self.site,
            system=self.system,
            drones=[self.drone],
            carts=[self.cart],
            active_mission=self.active_mission,
            active_incident=self.active_incident,
            open_incidents=self.open_incidents(),
            recent_missions=self.recent_missions(),
            camera=self.camera,
            server_time=now or datetime.now(timezone.utc),
        )


def create_default_store(settings: Settings, world: World) -> Store:
    """Boot state: the cart at the start plaza beside the First Aid Center (the hub),
    facing north, with the drone docked on its roof (docs/WORLD.md §3, §5)."""
    projection = Projection(settings)
    hub = _project(projection, world.first_aid_door)
    cart_position = _project(projection, world.start)
    site = Site(
        id="expo-2030",
        name=LocalizedText(en="Expo 2030 Riyadh", ar="إكسبو 2030 الرياض"),
        visitors=settings.site_visitors,
        map=SiteMap(view_box=ViewBox(w=1000, h=640), scale_bar_m=100),
        hub=hub,
        zones=["A", "B", "C", "D"],
    )
    system = SystemStatus(
        ros=RosStatus(ok=False, label="—"),
        jetson=JetsonStatus(ok=False, fps=0),
        backend=BackendStatus(ok=True, uptime_s=0),
    )
    drone = Drone(
        id=settings.drone_id,
        state="docked",
        battery_pct=100,
        flight_time_left_s=0,
        position=cart_position,
        altitude_m=0,
        heading_deg=0,
        speed_mps=0,
        docked_on_cart=True,
        payload=DronePayload(camera=True, speaker=True, led=True),
        detector=Detector(model="YOLOv8n", fps=0),
        # A mock drone is healthy from the start; a ROS drone stays "lost" until its
        # first /drone/status.
        link="lost" if settings.source_drone == "ros" else "ok",
    )
    cart = Cart(
        id=settings.cart_id, position=cart_position, heading_deg=round(compass_deg(world.start_heading_rad), 1),
        speed_kmh=0, state="at_hub",
    )
    camera = CameraInfo(
        drone_id=settings.drone_id,
        available=False,
        stream_url=f"/api/drones/{settings.drone_id}/camera/stream",
        protocol="mjpeg",
        mode="off",
        resolution=ImageSize(w=settings.camera_fallback_width, h=settings.camera_fallback_height),
    )
    return Store(site, system, drone, cart, camera, history_limit=settings.history_limit)


def _project(projection: Projection, point) -> Position:
    """World ENU point -> Position. Falls back to SVG x/y = metres when the projection
    has been switched off (SABEQ_PROJ_* emptied), so the app still boots."""
    if projection.configured:
        return projection.local_to_position(*point)
    return Position(lat=0.0, lon=0.0, x=point[0], y=point[1])
