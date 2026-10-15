"""WS /ws (§5): snapshot first, broadcasts, ping/pong, errors, ping timeout."""
import threading
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.config import Settings
from tests.conftest import manual_app
from app.models import SystemStatus


def _ts_ok(msg):
    assert set(msg) == {"type", "data", "ts"}
    datetime.fromisoformat(msg["ts"].replace("Z", "+00:00"))


def _until(ws, type_, limit=20):
    for _ in range(limit):
        msg = ws.receive_json()
        _ts_ok(msg)
        if msg["type"] == type_:
            return msg
    raise AssertionError(f"no {type_} message")


def test_snapshot_on_connect(client):
    with client.websocket_connect("/ws") as ws:
        msg = ws.receive_json()
        _ts_ok(msg)
        assert msg["type"] == "snapshot"
        assert msg["data"]["activeMission"] is None
        assert msg["data"]["drones"][0]["id"] == "SBQ-01"


def test_snapshot_includes_active_mission(drv, client):
    drv.create_incident()
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["data"]["activeMission"]["state"] == "incident_received"


def test_rest_changes_are_broadcast_in_order(drv, client):
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "snapshot"
        mid = drv.create_incident()["missionId"]
        created = ws.receive_json()
        assert created["type"] == "incident.created"
        assert created["data"]["openIncidents"] == {"active": 1, "queued": 0}
        state = ws.receive_json()
        assert state["type"] == "mission.state"
        assert state["data"]["from"] == "idle" and state["data"]["to"] == "incident_received"
        assert state["data"]["mission"]["id"] == mid

        drv.ok(mid, "launch")
        launched = _until(ws, "mission.state")
        assert (launched["data"]["from"], launched["data"]["to"]) == ("incident_received", "taking_off")


def test_route_and_close_messages(drv, client):
    mid = drv.drive_to("scanning")
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        drv.recommend("B")
        rec = _until(ws, "route.recommendation")
        assert rec["data"]["recommended"] == "B" and rec["data"]["countdownS"] == 60
        assert [r["id"] for r in rec["data"]["routes"]] == ["A", "B", "C"]
        drv.ok(mid, "route", {"route": "C"})
        sel = _until(ws, "route.selected")
        assert sel["data"] == {"missionId": mid, "route": "C", "source": "operator"}
        assert _until(ws, "payload.state")["data"] == {"missionId": mid, "speaker": True, "led": True}
        drv.ok(mid, "abort", {"reason": "operator_abort"})
        assert _until(ws, "mission.state")["data"]["reason"] == "operator_abort"
        drv.ok(mid, "close")
        hist = _until(ws, "mission.history")
        assert hist["data"]["item"]["outcome"] == "aborted"


def test_fault_sends_error_message(drv, client):
    drv.drive_to("taking_off")
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        drv.on_loop(drv.svc.fault, "link_lost", "No /drone/status for 5 s")
        state = _until(ws, "mission.state")
        assert state["data"]["to"] == "error"
        err = _until(ws, "error")
        assert err["data"]["code"] == "link_lost" and err["data"]["missionId"]


def test_ping_pong(client):
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "ping", "data": {"clientTs": "2026-10-06T12:00:00.000Z"}, "ts": "2026-10-06T12:00:00Z"})
        pong = ws.receive_json()
        _ts_ok(pong)
        assert pong["type"] == "pong" and pong["data"] == {"clientTs": "2026-10-06T12:00:00.000Z"}


@pytest.mark.parametrize(
    "payload",
    [
        "not json",
        '{"type": "ping"}',  # no data / ts
        '{"type": "launch", "data": {}, "ts": "2026-10-06T12:00:00Z"}',  # commands are REST only
        '{"type": "ping", "data": {}, "ts": "2026-10-06T12:00:00Z"}',  # missing clientTs
    ],
)
def test_bad_client_message_gets_error_and_stays_open(client, payload):
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_text(payload)
        err = ws.receive_json()
        assert err["type"] == "error" and err["data"]["code"] == "bad_request"
        ws.send_json({"type": "ping", "data": {"clientTs": "x"}, "ts": "2026-10-06T12:00:00Z"})
        assert ws.receive_json()["type"] == "pong"


def test_closes_without_ping():
    app = manual_app(Settings(_env_file=None, ws_ping_timeout_s=0.3, system_status_interval_s=3600))
    with TestClient(app) as c, c.websocket_connect("/ws") as ws:
        ws.receive_json()
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
        assert exc.value.code == 1000


def test_non_ping_messages_do_not_extend_timeout():
    app = manual_app(Settings(_env_file=None, ws_ping_timeout_s=0.4, system_status_interval_s=3600))
    with TestClient(app) as c, c.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_text("garbage")
        assert ws.receive_json()["type"] == "error"
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()


def test_periodic_system_status():
    app = manual_app(Settings(_env_file=None, system_status_interval_s=0.05))
    with TestClient(app) as c, c.websocket_connect("/ws") as ws:
        ws.receive_json()
        msg = _until(ws, "system.status")
        assert set(msg["data"]) == {"ros", "jetson", "backend"}


def test_broadcast_from_another_thread(app, client):
    """The broadcaster can be called from any thread (future ROS callbacks)."""
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        status = app.state.store.system.model_copy(deep=True)
        t = threading.Thread(target=app.state.broadcaster.publish, args=("system.status", status))
        t.start()
        t.join()
        assert SystemStatus.model_validate(_until(ws, "system.status")["data"])


def test_every_client_gets_broadcasts(drv, client):
    with client.websocket_connect("/ws") as a, client.websocket_connect("/ws") as b:
        a.receive_json(), b.receive_json()
        drv.create_incident()
        assert a.receive_json()["type"] == "incident.created"
        assert b.receive_json()["type"] == "incident.created"


def test_preselect_broadcasts_same_state_mission_state(drv, client):
    from tests.conftest import make_routes

    mid = drv.drive_to("scanning")
    drv.on_loop(drv.svc.route_scores, make_routes(), False)
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        drv.ok(mid, "route", {"route": "C"})
        msg = _until(ws, "mission.state")
        assert msg["data"]["from"] == msg["data"]["to"] == "scanning"
        assert msg["data"]["reason"] == "preselected"
        assert msg["data"]["mission"]["preselectedRoute"] == "C"
