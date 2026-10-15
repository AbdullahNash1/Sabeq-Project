from .base import ApiModel


class RosStatus(ApiModel):
    ok: bool
    label: str


class JetsonStatus(ApiModel):
    ok: bool
    fps: float


class BackendStatus(ApiModel):
    ok: bool
    uptime_s: float


class SystemStatus(ApiModel):
    """§3.2 — header status chips. WS latency is measured client-side."""

    ros: RosStatus
    jetson: JetsonStatus
    backend: BackendStatus
