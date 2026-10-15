"""Full v1 missions with all-mock sources, sped up with MOCK_SPEED.

Only REST + the mocks drive the mission here (no direct hook calls)."""
import time

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.ros.messages import MISSION_COMMAND
from app.sources.base import SourceSet
from app.sources.factory import build_sources
from app.sources.null import NullRouteSource
from tests.conftest import INCIDENT_BODY


def mock_settings(**overrides):
    base = dict(
        _env_file=None,
        mock_speed=150,
        mock_tick_s=0.01,
        auto_select_seconds=0.05,
        system_status_interval_s=3600,
        ws_queue_size=100_000,
    )
    return Settings(**{**base, **overrides})


def mock_app(sources_factory=None, **overrides):
    return create_app(mock_settings(**overrides), sources_factory=sources_factory, record_commands=True)


def wait_for(client, predicate, what, timeout=20.0):
    deadline = time.monotonic() + timeout
    mission = None
    while time.monotonic() < deadline:
        mission = client.get("/api/missions/active").json()
        if predicate(mission):
            return mission
        time.sleep(0.01)
    raise AssertionError(f"timed out waiting for {what}; last mission state: {mission and mission['state']}")


def wait_state(client, state, timeout=20.0):
    return wait_for(client, lambda m: m is not None and m["state"] == state, state, timeout)


def wait_drone_docked(client, timeout=20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        drone = client.get("/api/drones/SBQ-01").json()
        if drone["dockedOnCart"] and drone["state"] == "docked":
            return drone
        time.sleep(0.01)
    raise AssertionError(f"drone never docked: {drone}")


def start_mission(client):
    mid = client.post("/api/incidents", json=INCIDENT_BODY).json()["missionId"]
    assert client.post(f"/api/missions/{mid}/launch", json={}).status_code == 200
    return mid


def drain(ws, until_type):
    """All WS messages up to and including the first `until_type`."""
    messages = []
    for _ in range(100_000):
        msg = ws.receive_json()
        messages.append(msg)
        if msg["type"] == until_type:
            return messages
    raise AssertionError(f"no {until_type}")


WORLD_IDS = ["A_west", "B_north", "C_east"]
LABELS = [{"en": "A · West", "ar": "A · غرب"}, {"en": "B · North", "ar": "B · شمال"}, {"en": "C · East", "ar": "C · شرق"}]


def test_full_mission_start_to_completed():
    """launch -> scan -> C_east (Node 2's formula) -> escort -> rescue -> return to the
    First Aid Center -> the drone lands on the cart -> completed -> close.
    MOCK_SPEED 20: slow enough for the real-time 2 Hz escort.progress to cover both legs."""
    app = mock_app(mock_speed=20)
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "snapshot"
        mid = start_mission(client)

        wait_state(client, "scanning")
        m = wait_state(client, "escorting")
        assert [r["id"] for r in m["routes"]] == WORLD_IDS
        assert [r["label"] for r in m["routes"]] == LABELS
        # Node 2's formula on the scan-zone counts: distance / 7.0 + people x 0.5
        by_id = {r["id"]: r for r in m["routes"]}
        assert {k: (r["peopleCount"], r["estTimeS"]) for k, r in by_id.items()} == {
            "A_west": (18, 30.6), "B_north": (26, 29.0), "C_east": (10, 26.6),
        }
        assert {k: r["score"] for k, r in by_id.items()} == {"A_west": 0.869, "B_north": 0.917, "C_east": 1.0}
        assert all(r["crowdFactor"] is None and r["countFinal"] for r in m["routes"])
        assert m["recommendedRoute"] == m["selectedRoute"] == "C_east" and m["selectionSource"] == "auto"
        assert (m["returnPolyline"][0]["x"], m["returnPolyline"][-1]["y"]) == (517.5, 579.0)

        m = wait_state(client, "arrived")
        assert m["rescue"] == {"state": "in_progress", "patientLoadedAt": None}
        assert m["result"]["peopleWarned"] == 10 and m["result"]["vsBestAlternativeS"] == -2.4
        assert m["escort"]["progress"] == 1
        early = client.post(f"/api/missions/{mid}/return", json={})
        assert early.status_code == 409 and early.json()["error"] == {
            "code": "invalid_transition", "message": "Cannot return before the patient is loaded",
            "details": {"state": "arrived", "action": "return", "rescue": "in_progress"},
        }

        m = wait_for(client, lambda m: m["rescue"]["state"] == "patient_loaded", "patient loaded")
        assert m["state"] == "arrived" and m["rescue"]["patientLoadedAt"]
        assert client.post(f"/api/missions/{mid}/return", json={}).status_code == 200
        m = wait_state(client, "completed")
        assert m["rescue"]["state"] == "patient_loaded"
        drone = wait_drone_docked(client)
        cart = client.get("/api/carts/CART-01").json()
        # Cart stopped at the First Aid Center (0, -15); the drone sits on it.
        assert cart["state"] == "at_hub"
        assert (cart["position"]["x"], cart["position"]["y"]) == (500.0, 526.5 + 15 * 3.5)
        assert (drone["position"]["x"], drone["position"]["y"]) == (cart["position"]["x"], cart["position"]["y"])
        closed = client.post(f"/api/missions/{mid}/close", json={"outcome": "resolved"}).json()
        assert closed["state"] == "idle" and closed["outcome"] == "resolved"
        assert client.get("/api/missions/history").json()[0]["routeLabel"] == {"en": "C · East", "ar": "C · شرق"}
        assert [e["state"] for e in closed["timeline"]] == [
            "incident_received", "taking_off", "scanning", "route_selected",
            "escorting", "arrived", "returning", "completed",
        ]

        messages = drain(ws, "mission.history")

    by_type: dict = {}
    for msg in messages:
        by_type.setdefault(msg["type"], []).append(msg["data"])

    # Scan: the live counts only grow, then final scores
    scores = by_type["route.scores"]
    assert len(scores) >= 2 and scores[-1]["final"] is True
    for rid in WORLD_IDS:
        series = [next(r["peopleCount"] for r in s["routes"] if r["id"] == rid) for s in scores]
        assert series == sorted(series) and series[0] < series[-1]
    assert {p["phase"] for p in by_type["scan.progress"]} >= {"climb", "rotate", "done"}
    assert all(p["altitudeM"] <= 8 for p in by_type["scan.progress"])
    assert by_type["route.recommendation"][0]["countdownS"] == 0.05

    # Same-state mission.state events: rescue, then the cart at the hub, then completed
    reasons = [(e["to"], e["reason"]) for e in by_type["mission.state"]]
    assert reasons.index(("arrived", "patient_loaded")) < reasons.index(("returning", "cart_at_hub")) \
        < reasons.index(("completed", None))

    # Escort both ways, drone ahead of the cart
    legs = {e["leg"] for e in by_type["escort.progress"]}
    assert legs == {"outbound", "return"}
    assert any((e["droneAheadOfCartM"] or 0) > 5 for e in by_type["escort.progress"])
    cart_states = {c["state"] for c in by_type["cart.telemetry"]}
    assert {"to_incident", "at_incident", "to_hub", "at_hub"} <= cart_states
    assert max(c["speedKmh"] for c in by_type["cart.telemetry"]) == 36.0  # 10 m/s
    drone_states = {d["state"] for d in by_type["drone.telemetry"]}
    assert {"taking_off", "scanning", "escorting", "landing", "docked"} <= drone_states

    # Camera: boxes during the scan (with per-route counts) and during the escort
    detections = by_type["camera.detections"]
    assert any(d["mode"] == "scan_360" and d["boxes"] for d in detections)
    assert any(d["mode"] == "forward" and d["boxes"] and d["counts"] is None for d in detections)
    for d in detections:
        assert d["imageSize"] == {"w": 1280, "h": 960}
        assert all(0 <= b["x"] <= 100 and 0 <= b["y"] <= 100 for b in d["boxes"])

    assert by_type["mission.history"][0]["item"]["outcome"] == "resolved"
    commands = app.state.recorder.on(MISSION_COMMAND)
    assert [c["command"] for c in commands] == ["launch", "select_route", "start_return"]
    assert commands[1]["polylineLocalM"] == [[0.0, 0.0], [36.0, 36.0], [36.0, 92.0], [16.0, 112.0], [0.0, 112.0]]
    assert commands[2]["polylineLocalM"] == [[5.0, 112.0], [16.0, 112.0], [36.0, 92.0], [36.0, 36.0],
                                             [0.0, 0.0], [0.0, -15.0]]


def test_operator_abort_drone_lands_on_cart():
    app = mock_app()
    with TestClient(app) as client:
        mid = start_mission(client)
        wait_for(client, lambda m: m["state"] == "escorting" and m["escort"]["progress"] > 0.2, "escort under way")
        m = client.post(f"/api/missions/{mid}/abort", json={"reason": "return_to_dock"}).json()
        assert m["state"] == "aborted" and m["abortReason"] == "return_to_dock"

        drone = wait_drone_docked(client)
        cart = client.get("/api/carts/CART-01").json()
        assert cart["state"] == "stopped"
        assert (drone["position"]["x"], drone["position"]["y"]) == (cart["position"]["x"], cart["position"]["y"])

        closed = client.post(f"/api/missions/{mid}/close", json={}).json()
        assert closed["outcome"] == "aborted"
        assert client.get("/api/missions/history").json()[0]["outcome"] == "aborted"
        # The operator's manual drive back is simulated by the mock cart.
        deadline = time.monotonic() + 5
        while client.get("/api/carts/CART-01").json()["state"] != "at_hub":
            assert time.monotonic() < deadline
            time.sleep(0.01)


def test_low_battery_abort_mid_escort():
    app = mock_app(mock_low_battery=True)
    with TestClient(app) as client:
        mid = start_mission(client)
        m = wait_state(client, "aborted")
        assert m["abortReason"] == "low_battery"
        assert m["escort"]["leg"] == "outbound" and m["escort"]["progress"] >= 0.45
        assert [e["state"] for e in m["timeline"]][-2:] == ["escorting", "aborted"]
        assert app.state.recorder.on(MISSION_COMMAND)[-1]["reason"] == "low_battery"
        wait_drone_docked(client)
        assert client.post(f"/api/missions/{mid}/close", json={}).json()["outcome"] == "aborted"


def test_preselection_during_scan():
    app = mock_app(mock_speed=8)  # slow enough to pre-select while the scan runs
    with TestClient(app) as client:
        mid = start_mission(client)
        wait_for(client, lambda m: m["state"] == "scanning" and len(m["routes"]) == 3, "partial route scores")
        m = client.post(f"/api/missions/{mid}/route", json={"route": "A_west"}).json()  # not the winner
        assert m["state"] == "scanning" and m["preselectedRoute"] == "A_west"
        m = wait_state(client, "escorting")
        assert m["recommendedRoute"] == "C_east"
        assert m["selectedRoute"] == "A_west" and m["selectionSource"] == "operator"
        assert m["preselectedRoute"] == "A_west" and m["autoSelectAt"] is None
        assert app.state.recorder.on(MISSION_COMMAND)[-1]["route"] == "A_west"


def test_scan_headings_and_detections_carry_route_counts():
    """Slow enough that several detections fall inside the scan (WS rate is real-time capped)."""
    app = mock_app(mock_speed=8)
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        ws.receive_json()
        start_mission(client)
        messages = drain(ws, "route.recommendation")
    # The drone held each route's scan yaw from world_meta.json (NED 315, 0, 45), in scan order
    held = [round(m["data"]["headingDeg"]) for m in messages
            if m["type"] == "scan.progress" and m["data"]["phase"] == "rotate"]
    order = [h for i, h in enumerate(held) if h in (315, 0, 45) and (i == 0 or held[i - 1] != h)]
    assert order[:3] == [315, 0, 45]
    # Counts follow Node 2's /scan/active_route semantics: a route only counts on its turn,
    # never while the drone passes it turning to the next one (it starts facing B_north).
    for msg in messages:
        if msg["type"] == "route.scores" and not msg["data"]["final"]:
            c = {r["id"]: r["peopleCount"] for r in msg["data"]["routes"]}
            assert c["B_north"] == 0 or c["A_west"] == 18, c
            assert c["C_east"] == 0 or c["B_north"] == 26, c
    scans = [m["data"] for m in messages if m["type"] == "camera.detections" and m["data"]["mode"] == "scan_360"]
    with_counts = [d for d in scans if d["counts"]]
    assert with_counts and all(set(d["counts"]) == set(WORLD_IDS) for d in with_counts)
    assert max(len(d["boxes"]) for d in scans) > 0  # boxes for the route being scanned


def test_operator_override_during_countdown():
    app = mock_app(auto_select_seconds=30)
    with TestClient(app) as client:
        mid = start_mission(client)
        m = wait_state(client, "route_selected")
        other = next(r["id"] for r in m["routes"] if r["id"] != m["recommendedRoute"])
        m = client.post(f"/api/missions/{mid}/route", json={"route": other}).json()
        assert m["state"] == "escorting" and m["selectedRoute"] == other and m["selectionSource"] == "operator"


@pytest.mark.parametrize("best", WORLD_IDS)
def test_mock_best_route_forces_winner(best):
    with TestClient(mock_app(mock_best_route=best, auto_select_seconds=30)) as client:
        start_mission(client)
        m = wait_state(client, "route_selected")
        assert m["recommendedRoute"] == best
        assert min(m["routes"], key=lambda r: r["estTimeS"])["id"] == best


def test_default_crowd_is_the_scan_zone_from_world_meta():
    """No randomness: the counts are the people within 36 m of the start in world_meta.json."""
    with TestClient(mock_app(auto_select_seconds=30)) as client:
        start_mission(client)
        m = wait_state(client, "route_selected")
    assert [(r["id"], r["peopleCount"]) for r in m["routes"]] == [("A_west", 18), ("B_north", 26), ("C_east", 10)]
    assert m["recommendedRoute"] == "C_east"


def test_parts_are_independent():
    """Mock drone/cart/camera with a route source that never answers: the scan
    completes, but the mission waits in `scanning` for a route recommendation."""

    def factory(settings, service, store):
        built = build_sources(settings, service, store)
        return SourceSet(built.camera, NullRouteSource(service, store, settings), built.drone, built.cart)

    with TestClient(mock_app(sources_factory=factory)) as client:
        start_mission(client)
        wait_for(client, lambda m: m["scan"]["phase"] == "done", "scan done")
        time.sleep(0.3)
        m = client.get("/api/missions/active").json()
        assert m["state"] == "scanning" and m["routes"] == []


def test_mock_counts_only_the_route_being_scanned(drv, svc, app):
    """Unit check of the mock Node 2: the drone starts the scan facing B_north (0°) while it
    turns to A_west; only the route whose turn it is counts, once the drone faces it."""
    from app.sources.commands import IncidentCommand
    from app.sources.mock.route import MockRouteSource

    source = MockRouteSource(svc, svc.store, app.state.settings)
    mid = drv.drive_to("scanning")
    source.handle(IncidentCommand(active=True, mission_id=mid))
    drone, mission = svc.store.drone, svc.store.active_mission
    drone.state = "scanning"

    def at(coverage_pct, heading):
        mission.scan.phase, mission.scan.coverage_pct, drone.heading_deg = "rotate", coverage_pct, heading
        drv.on_loop(source._step, 0.5)
        return {rid: int(n) for rid, n in source._counts.items()}

    assert at(0.0, 0.0) == {"A_west": 0, "B_north": 0, "C_east": 0}  # turning to A, passing B
    assert at(0.0, 40.0) == {"A_west": 0, "B_north": 0, "C_east": 0}  # near C, still not its turn
    assert at(0.0, 315.0)["A_west"] > 0  # facing A on its turn
    a_done = at(33.3, 315.0)  # A's turn is over (coverage rounded to 0.1)
    assert a_done["B_north"] == 0
    assert at(33.3, 0.0)["B_north"] > 0 and source._counts["A_west"] == a_done["A_west"]
    assert at(66.7, 0.0)["C_east"] == 0  # B's turn over, not yet facing C
    assert at(66.7, 45.0)["C_east"] > 0
    mission.scan.phase = "climb"
    before = dict(source._counts)
    drv.on_loop(source._step, 0.5)
    assert source._counts == before  # nothing counts outside the rotation
