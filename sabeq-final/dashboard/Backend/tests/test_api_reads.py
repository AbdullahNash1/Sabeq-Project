"""§4.1 snapshot, §4.2 site/system/fleet, §4.3 incidents."""
import pytest

from app.config import Settings
from app.main import create_app
from fastapi.testclient import TestClient
from tests.conftest import INCIDENT_BODY, manual_app


def _envelope(resp, status, code):
    assert resp.status_code == status, resp.text
    assert resp.json()["error"]["code"] == code


def test_snapshot_idle(client):
    snap = client.get("/api/snapshot").json()
    assert set(snap) == {"site", "system", "drones", "carts", "activeMission", "activeIncident",
                         "openIncidents", "recentMissions", "camera", "serverTime"}
    assert snap["activeMission"] is None and snap["activeIncident"] is None
    assert snap["openIncidents"] == {"active": 0, "queued": 0}
    assert [d["id"] for d in snap["drones"]] == ["SBQ-01"]
    assert [c["id"] for c in snap["carts"]] == ["CART-01"]


def test_snapshot_with_mission(drv, client):
    drv.create_incident()
    drv.create_incident()
    snap = client.get("/api/snapshot").json()
    assert snap["activeMission"]["state"] == "incident_received"
    assert snap["openIncidents"] == {"active": 1, "queued": 1}


def test_site_visitors_null_unless_configured(client):
    assert client.get("/api/site").json()["visitors"] is None
    with TestClient(manual_app(Settings(_env_file=None, site_visitors=41280))) as c:
        assert c.get("/api/site").json()["visitors"] == 41280


def test_site_and_system(client):
    assert client.get("/api/site").json()["map"] == {"viewBox": {"w": 1000, "h": 640}, "scaleBarM": 100}
    status = client.get("/api/system/status").json()
    assert set(status) == {"ros", "jetson", "backend"}
    assert status["backend"]["ok"] is True and status["backend"]["uptimeS"] >= 0


def test_drones_and_carts(client):
    assert len(client.get("/api/drones").json()) == 1
    drone = client.get("/api/drones/SBQ-01").json()
    assert drone["dockedOnCart"] is True and drone["link"] == "ok"  # ROS off -> healthy
    assert len(client.get("/api/carts").json()) == 1
    assert client.get("/api/carts/CART-01").json()["state"] == "at_hub"
    _envelope(client.get("/api/drones/SBQ-02"), 404, "not_found")
    _envelope(client.get("/api/carts/CART-02"), 404, "not_found")


def test_drone_link_lost_until_ros_drone_reports():
    from app.state.store import create_default_store
    from app.world import load_world

    for mode, link in (("ros", "lost"), ("mock", "ok")):
        settings = Settings(_env_file=None, source_drone=mode)
        assert create_default_store(settings, load_world(settings)).drone.link == link


def test_camera(client):
    cam = client.get("/api/drones/SBQ-01/camera").json()
    assert cam == {"droneId": "SBQ-01", "available": False, "streamUrl": "/api/drones/SBQ-01/camera/stream",
                   "protocol": "mjpeg", "mode": "off", "resolution": {"w": 1280, "h": 960}}
    _envelope(client.get("/api/drones/SBQ-01/camera/stream"), 503, "ros_unavailable")  # null camera
    _envelope(client.get("/api/drones/NOPE/camera"), 404, "not_found")


# --- incidents ----------------------------------------------------------------


def test_create_incident_activates_when_idle(drv, client, ros):
    inc = drv.create_incident()
    assert inc["id"] == "INC-0001" and inc["status"] == "active" and inc["missionId"] == "MSN-0001"
    assert inc["type"] == {"code": "cardiac_arrest", "label": INCIDENT_BODY["title"]}
    # Always the world incident (0, 112), whatever was posted (x 750, y 235).
    pos = inc["location"]["position"]
    assert (pos["x"], pos["y"]) == (500.0, pytest.approx(134.5))
    assert pos["lat"] == pytest.approx(24.9582 + 112 / 111_320) and pos["lon"] == pytest.approx(46.6495)
    assert inc["location"]["zone"] == "B" and inc["location"]["description"]["en"] == "Zone B · near Pavilion B-14"
    assert inc["distanceFromHubM"] == 127.3  # First Aid Center door (8.6, -15) -> incident
    assert inc["reportedAt"]
    topic, msg = ros.sent[-1]
    assert topic == "/incident" and msg["active"] is True and msg["localM"] == {"e": 0.0, "n": 112.0}


def test_create_incident_queues_when_busy(drv, client):
    drv.create_incident()
    queued = drv.create_incident()
    assert queued["status"] == "queued" and queued["missionId"] is None
    ids = [i["id"] for i in client.get("/api/incidents?status=queued").json()]
    assert ids == [queued["id"]]
    assert len(client.get("/api/incidents?status=active,queued").json()) == 2
    assert len(client.get("/api/incidents").json()) == 2


def test_get_incident(drv, client):
    inc = drv.create_incident()
    assert client.get(f"/api/incidents/{inc['id']}").json()["id"] == inc["id"]
    _envelope(client.get("/api/incidents/INC-9999"), 404, "not_found")


def test_incident_is_always_the_world_incident(drv, client):
    """lat/lon-only and x/y-only bodies both land on the world incident (0, 112)."""
    first = drv.create_incident()["location"]["position"]
    body = {**INCIDENT_BODY, "location": {"zone": "C", "description": {"en": "x"}, "lat": 24.9, "lon": 46.6}}
    queued = client.post("/api/incidents", json=body).json()
    assert queued["status"] == "queued" and queued["location"]["position"] == first


@pytest.mark.parametrize(
    "patch",
    [
        {"externalRef": "RADIO-7781"},  # deferred to v2 -> unknown field
        {"severity": "urgent"},
        {"source": "twitter"},
        {"title": {}},
        {"location": {"zone": "B", "description": {"en": "x"}}},  # no coordinates
        {"location": {"zone": "B", "description": {"en": "x"}, "lat": 24.9}},  # half a pair
    ],
)
def test_create_incident_400(client, patch):
    _envelope(client.post("/api/incidents", json={**INCIDENT_BODY, **patch}), 400, "bad_request")


def test_projection_corners_fit_the_map():
    """§2: the site bounds x -95..95, y -32..150 m fill the 1000 x 640 map, north up."""
    from app.geo import Projection

    proj = Projection(Settings(_env_file=None))
    for (e, n), (x, y) in {(-95, 150): (167.5, 1.5), (95, -32): (832.5, 638.5), (0, 0): (500, 526.5)}.items():
        p = proj.local_to_position(e, n)
        assert (p.x, p.y) == (pytest.approx(x), pytest.approx(y))
        assert proj.svg_to_local(p.x, p.y) == (pytest.approx(e), pytest.approx(n))
    assert proj.latlon_to_local(*(lambda p: (p.lat, p.lon))(proj.local_to_position(30, 40))) == (
        pytest.approx(30), pytest.approx(40))


def test_bad_status_filter_is_400(client):
    _envelope(client.get("/api/incidents?status=active,nope"), 400, "bad_request")


def test_cors_for_vite(client):
    resp = client.options(
        "/api/snapshot",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"},
    )
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == "http://localhost:5173"
