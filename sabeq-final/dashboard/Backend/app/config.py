from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

SourceMode = Literal["mock", "ros"]

# Backend/ — relative WORLD_META / ROUTES_YAML paths resolve against it, not the cwd.
BACKEND_DIR = Path(__file__).resolve().parents[1]

# Localised display label per route id ({en, ar}, like the other localised fields)
DEFAULT_ROUTE_LABELS = {
    "A_west": {"en": "A · West", "ar": "A · غرب"},
    "B_north": {"en": "B · North", "ar": "B · شمال"},
    "C_east": {"en": "C · East", "ar": "C · شرق"},
}


def _env(name: str, default, **kw):
    """Field read from NAME or SABEQ_NAME (the data-source/mock/dev names are unprefixed)."""
    return Field(default, validation_alias=AliasChoices(name, f"SABEQ_{name}"), **kw)


class Settings(BaseSettings):
    """Backend settings. Every field can be overridden with SABEQ_<NAME> or .env;
    data-source, mock and dev settings also accept the unprefixed names below."""

    model_config = SettingsConfigDict(
        env_prefix="SABEQ_",
        env_file=".env",
        env_ignore_empty=True,
        extra="ignore",
        validate_by_name=True,
    )

    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    # WebSocket (§5)
    ws_ping_timeout_s: float = 30.0
    ws_queue_size: int = 256  # per-client buffer; a client that falls this far behind is dropped
    system_status_interval_s: float = 2.0

    # Data sources, independently per part (rclpy comes from the ROS Humble apt install)
    source_camera: SourceMode = _env("SOURCE_CAMERA", "mock")  # MJPEG stream + detections
    source_route: SourceMode = _env("SOURCE_ROUTE", "mock")  # route scores + selected route
    source_drone: SourceMode = _env("SOURCE_DRONE", "mock")  # drone status/position + events
    source_cart: SourceMode = _env("SOURCE_CART", "mock")  # cart pose + status

    # World (docs/WORLD.md): route names, waypoints, incident, start, First Aid Center and
    # scan yaws come ONLY from these two files, checked against each other at startup.
    world_meta: str = _env("WORLD_META", "../docs/team/world_meta.json")
    routes_yaml: str = _env("ROUTES_YAML", "../ros_ws/src/sabeq_ros/config/routes.yaml")
    # Localised display label per route id (Route.label, {en, ar}); every route in routes.yaml needs one.
    route_labels: dict[str, dict[str, str]] = _env("ROUTE_LABELS", DEFAULT_ROUTE_LABELS)

    # ROS (any SOURCE_*=ros): the backend's own node; ROS stamps and simTimeS are sim time
    ros_node_name: str = "backend_bridge"
    ros_use_sim_time: bool = True

    # Mock simulation
    mock_speed: float = _env("MOCK_SPEED", 1.0, gt=0)  # sim-time multiplier for demos/tests
    mock_low_battery: bool = _env("MOCK_LOW_BATTERY", False)  # low-battery abort mid-escort
    # A route id from routes.yaml (checked at startup); None = Node 2's formula decides (C_east)
    mock_best_route: str | None = _env("MOCK_BEST_ROUTE", None)
    mock_tick_s: float = _env("MOCK_TICK_S", 0.1, gt=0)  # real seconds between mock ticks

    # Site facts with no live source yet (null in the API when unset)
    site_visitors: int | None = _env("SITE_VISITORS", None, ge=0)

    # Dev endpoints (POST /api/dev/sim/reset)
    dev_mode: bool = _env("DEV_MODE", False)

    # Fixed v1 fleet (API_CONTRACT §0: one drone, one cart)
    drone_id: str = "SBQ-01"
    cart_id: str = "CART-01"

    # Mission timing (§6, ROS_TOPICS §4.8 / §4.11)
    auto_select_seconds: float = 3.0
    heartbeat_timeout_s: float = 5.0
    command_ack_timeout_s: float = 3.0  # wait this long for command_ack from each ROS node
    scan_altitude_m: float = 8.0  # mission.py ALT: above the cart roof
    history_limit: int = 10

    # Camera (§3.9, ROS_TOPICS §4.1 / §4.2)
    mjpeg_max_fps: float = 15.0
    # raw: /camera/image (Python downscales + encodes every frame). compressed: JPEG from
    # image_transport republish on /camera/image/compressed, streamed as is. auto: compressed,
    # falling back to raw when no JPEG frame arrives within a few seconds.
    camera_transport: Literal["raw", "compressed", "auto"] = "raw"
    detections_max_hz: float = 10.0
    camera_fallback_width: int = 1280
    camera_fallback_height: int = 960

    # Coordinate projection (§2): the site bounds x -95..95, y -32..150 m (docs/WORLD.md)
    # fitted into the 1000 x 640 map, north up. lat0/lon0 are a placeholder anchor:
    # the simulated world has no real GPS position.
    proj_lat0: float | None = 24.9582
    proj_lon0: float | None = 46.6495
    proj_x0: float | None = 500.0
    proj_y0: float | None = 526.5
    proj_scale: float | None = 3.5  # SVG units per metre
    proj_rotation_deg: float = 0.0  # map-up vs north, clockwise

    def resolve_path(self, value: str) -> Path:
        path = Path(value).expanduser()
        return path if path.is_absolute() else (BACKEND_DIR / path).resolve()


@lru_cache
def get_settings() -> Settings:
    return Settings()
