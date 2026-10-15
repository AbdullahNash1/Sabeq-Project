"""Source plumbing: camera frames, MJPEG framing, ROS stubs, config, dev reset."""
import asyncio
import io
import math
import sys
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.config import Settings
from app.main import create_app
from app.routers.fleet import mjpeg
from app.sources.mock.camera import render_frame
from app.sources.null import null_sources
from app.world import WorldConfigError
from tests.conftest import INCIDENT_BODY

# --- camera -----------------------------------------------------------------------


def test_mock_frame_is_4_3_jpeg():
    jpeg = render_frame(["MSN-0001", "scanning"], 151.2, "scan_360", datetime(2026, 10, 6, 12, 0))
    img = Image.open(io.BytesIO(jpeg))
    assert img.format == "JPEG" and img.size == (1280, 960)


def test_mjpeg_framing():
    async def frames():
        yield b"\xff\xd8one"
        yield b"\xff\xd8two"

    async def collect():
        return [chunk async for chunk in mjpeg(frames())]

    chunks = asyncio.run(collect())
    assert chunks[0] == b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: 5\r\n\r\n\xff\xd8one\r\n"
    assert len(chunks) == 2


def test_mock_camera_frames_paced_by_max_fps():
    settings = Settings(_env_file=None, mjpeg_max_fps=20, system_status_interval_s=3600)
    app = create_app(settings)

    async def first_frames():
        camera = app.state.sources.camera
        stream = camera.frames()
        loop = asyncio.get_running_loop()
        start = loop.time()
        frames = [await stream.__anext__() for _ in range(3)]
        await stream.aclose()
        return frames, loop.time() - start

    frames, elapsed = asyncio.run(first_frames())
    assert all(f.startswith(b"\xff\xd8") for f in frames)
    assert elapsed >= 2 / 20 * 0.9


def test_stream_503_without_camera_source():
    app = create_app(Settings(_env_file=None), sources_factory=null_sources)
    with TestClient(app) as c:
        resp = c.get("/api/drones/SBQ-01/camera/stream")
        assert resp.status_code == 503 and resp.json()["error"]["code"] == "ros_unavailable"


def test_mock_camera_reports_available():
    with TestClient(create_app(Settings(_env_file=None, system_status_interval_s=3600))) as c:
        cam = c.get("/api/drones/SBQ-01/camera").json()
        assert cam["available"] is True and cam["resolution"] == {"w": 1280, "h": 960}


# --- ROS stubs and config -----------------------------------------------------------


@pytest.mark.parametrize("part", ["camera", "route", "drone", "cart"])
def test_ros_source_without_ros_fails_at_startup_with_a_clear_error(part, monkeypatch):
    """rclpy is imported lazily: create_app works on a machine without ROS; starting a ROS
    source explains how to get ROS instead of crashing with an ImportError."""
    from app.ros.transport import RosUnavailable
    monkeypatch.setitem(sys.modules, "rclpy", None)   # no ROS on this machine
    app = create_app(Settings(_env_file=None, **{f"source_{part}": "ros"}))
    with pytest.raises(RosUnavailable, match="/opt/ros/humble/setup.bash"):
        with TestClient(app):
            pass


def test_all_mock_never_imports_rclpy(monkeypatch):
    monkeypatch.setitem(sys.modules, "rclpy", None)
    with TestClient(create_app(Settings(_env_file=None))) as c:
        assert c.get("/api/system/status").status_code == 200


def test_env_names(monkeypatch):
    monkeypatch.setenv("SOURCE_CAMERA", "ros")
    monkeypatch.setenv("SABEQ_SOURCE_ROUTE", "ros")
    monkeypatch.setenv("MOCK_SPEED", "4")
    monkeypatch.setenv("MOCK_LOW_BATTERY", "true")
    monkeypatch.setenv("MOCK_BEST_ROUTE", "B_north")
    monkeypatch.setenv("DEV_MODE", "true")
    s = Settings(_env_file=None)
    assert (s.source_camera, s.source_route, s.source_drone, s.source_cart) == ("ros", "ros", "mock", "mock")
    assert s.mock_speed == 4 and s.mock_low_battery and s.mock_best_route == "B_north" and s.dev_mode


@pytest.mark.parametrize("best", ["D", "B"])  # unknown, and the old v1.3 letter ids
def test_mock_best_route_must_be_a_world_route(best):
    with pytest.raises(WorldConfigError, match="MOCK_BEST_ROUTE"):
        create_app(Settings(_env_file=None, mock_best_route=best))


# --- dev reset ------------------------------------------------------------------------


def test_dev_reset_hidden_without_dev_mode():
    with TestClient(create_app(Settings(_env_file=None), sources_factory=null_sources)) as c:
        resp = c.post("/api/dev/sim/reset")
        assert resp.status_code == 404 and resp.json()["error"]["code"] == "not_found"


def test_dev_reset():
    settings = Settings(_env_file=None, dev_mode=True, mock_speed=50, mock_tick_s=0.01,
                        system_status_interval_s=3600, ws_queue_size=100_000)
    with TestClient(create_app(settings)) as c, c.websocket_connect("/ws") as ws:
        ws.receive_json()
        first = c.post("/api/incidents", json=INCIDENT_BODY).json()
        c.post("/api/incidents", json=INCIDENT_BODY)
        c.post(f"/api/missions/{first['missionId']}/launch", json={})
        snap = c.post("/api/dev/sim/reset").json()
        assert snap["activeMission"] is None and snap["openIncidents"] == {"active": 0, "queued": 0}
        assert snap["recentMissions"] == []
        for _ in range(10_000):
            msg = ws.receive_json()
            if msg["type"] == "snapshot":
                assert msg["data"]["activeMission"] is None
                break
        else:
            raise AssertionError("no snapshot broadcast after reset")
        # Fresh ids, docked drone, and a new mission runs normally.
        again = c.post("/api/incidents", json=INCIDENT_BODY).json()
        assert again["id"] == "INC-0001" and again["missionId"] == "MSN-0001"
        drone = c.get("/api/drones/SBQ-01").json()
        assert drone["dockedOnCart"] and drone["state"] == "docked" and math.isclose(drone["altitudeM"], 0)
        assert c.get("/api/drones/SBQ-01/camera").json()["available"] is True  # mock camera survives a reset
        assert c.post(f"/api/missions/{again['missionId']}/launch", json={}).status_code == 200
