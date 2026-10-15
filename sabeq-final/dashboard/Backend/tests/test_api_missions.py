"""§4.4 mission endpoints + §6.1 side effects."""
import time

import pytest

from app.ros.messages import INCIDENT, MISSION_COMMAND, PAYLOAD_CMD
from app.state.machine import COMMAND_STATES, Command, MissionState
from tests.conftest import make_routes

ALL_STATES = [s.value for s in MissionState if s is not MissionState.IDLE]

# A valid body per command, so the only failure reason is the state.
BODIES = {
    "launch": {},
    "dismiss": {},
    "route": {"route": "B"},
    "payload": {"speaker": False},
    "abort": {"reason": "operator_abort"},
    "return": {},
    "close": {"outcome": "resolved"},
}


def _envelope(resp, status, code):
    assert resp.status_code == status, resp.text
    body = resp.json()
    assert set(body) == {"error"} and body["error"]["code"] == code
    return body["error"]


# --- reads --------------------------------------------------------------------


def test_active_is_200_null_when_idle(client):
    resp = client.get("/api/missions/active")
    assert resp.status_code == 200
    assert resp.json() is None


def test_get_mission_and_404(drv, client):
    mid = drv.drive_to("incident_received")
    assert client.get(f"/api/missions/{mid}").json()["id"] == mid
    _envelope(client.get("/api/missions/MSN-9999"), 404, "not_found")


def test_history_empty_then_filled(drv, client):
    assert client.get("/api/missions/history").json() == []
    mid = drv.drive_to("incident_received")
    drv.ok(mid, "dismiss")
    [item] = client.get("/api/missions/history").json()
    assert item == {
        "id": mid, "reportedAt": item["reportedAt"],
        "incidentType": {"code": "cardiac_arrest", "label": {"en": "Suspected cardiac arrest", "ar": "اشتباه توقف القلب"}},
        "zone": "B", "route": None, "routeLabel": None, "responseTimeS": None, "droneId": None, "outcome": "cancelled",
    }


def test_history_keeps_last_10(drv, client):
    for _ in range(12):
        drv.ok(drv.create_incident()["missionId"], "dismiss")
    ids = [m["id"] for m in client.get("/api/missions/history").json()]
    assert len(ids) == 10 and ids[0] == "MSN-0012" and ids[-1] == "MSN-0003"
    _envelope(client.get("/api/missions/MSN-0001"), 404, "not_found")
    assert client.get("/api/missions/MSN-0003").json()["outcome"] == "cancelled"


# --- happy paths --------------------------------------------------------------


def test_launch(drv, ros):
    mid = drv.drive_to("incident_received")
    m = drv.ok(mid, "launch")
    assert m["state"] == "taking_off" and m["scan"]["phase"] == "climb"
    cmd = ros.on(MISSION_COMMAND)[-1]
    assert cmd["command"] == "launch" and cmd["scanAltitudeM"] == 8 and cmd["missionId"] == mid


def test_launch_without_body(drv, client):
    mid = drv.drive_to("incident_received")
    assert client.post(f"/api/missions/{mid}/launch").json()["state"] == "taking_off"


def test_launch_503_when_drone_not_ready(drv, svc):
    mid = drv.drive_to("incident_received")
    svc.store.drone.link = "lost"
    _envelope(drv.post(mid, "launch", {}), 503, "drone_unavailable")
    svc.store.drone.link = "ok"
    svc.store.drone.docked_on_cart = False
    _envelope(drv.post(mid, "launch", {}), 503, "drone_unavailable")


def test_dismiss(drv, client, ros):
    mid = drv.drive_to("incident_received")
    m = drv.ok(mid, "dismiss")
    assert m["state"] == "idle" and m["outcome"] == "cancelled" and m["endedAt"]
    assert client.get("/api/missions/active").json() is None
    assert client.get(f"/api/incidents/{m['incidentId']}").json()["status"] == "cancelled"
    assert ros.on(INCIDENT)[-1] == {"active": False, "missionId": None, "simTimeS": None}


def test_route_override_locks_and_starts_escort(drv, ros):
    mid = drv.drive_to("route_selected")
    m = drv.ok(mid, "route", {"route": "C"})
    assert m["state"] == "escorting"
    assert m["selectedRoute"] == "C" and m["selectionSource"] == "operator"
    assert m["escort"] == {"leg": "outbound", "progress": 0, "elapsedS": 0, "etaS": 170.0, "droneAheadOfCartM": None}
    assert m["payload"] == {"speaker": True, "led": True}
    cmd = ros.on(MISSION_COMMAND)[-1]
    assert cmd["command"] == "select_route" and cmd["route"] == "C" and cmd["source"] == "operator"
    assert cmd["polylineLocalM"] == [[0.0, 0.0], [0.0, 112.0]]  # the route's own polyline (not a world route)


def test_route_preselect_during_scanning(drv):
    mid = drv.drive_to("scanning")
    # Pre-selection needs the route list, which arrives with the route scores.
    assert drv.post(mid, "route", {"route": "A"}).status_code == 400
    drv.on_loop(drv.svc.route_scores, make_routes(), False)
    m = drv.ok(mid, "route", {"route": "A"})
    assert m["state"] == "scanning" and m["preselectedRoute"] == "A"
    m = drv.recommend("B")
    assert m.state.value == "escorting"
    assert m.selected_route == "A" and m.selection_source == "operator"
    assert m.auto_select_at is None


@pytest.mark.parametrize("routes", [("A", "B", "C"), ("A", "B", "C", "D")])
def test_route_ids_are_dynamic(drv, routes):
    mid = drv.drive_to("scanning")
    drv.recommend("B", routes)
    assert drv.ok(mid, "route", {"route": routes[-1]})["selectedRoute"] == routes[-1]


def test_route_not_in_list_is_400(drv):
    mid = drv.drive_to("route_selected")
    err = _envelope(drv.post(mid, "route", {"route": "Z"}), 400, "bad_request")
    assert "not one of" in err["message"]


def test_auto_select_after_countdown(settings, drv, client):
    settings.auto_select_seconds = 0.05
    mid = drv.drive_to("route_selected")
    m = client.get("/api/missions/active").json()
    assert m["autoSelectAt"] and m["recommendedRoute"] == "B"
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        m = client.get(f"/api/missions/{mid}").json()
        if m["state"] == "escorting":
            break
        time.sleep(0.02)
    assert m["state"] == "escorting"
    assert m["selectedRoute"] == "B" and m["selectionSource"] == "auto"


def test_override_cancels_countdown(settings, drv, client):
    settings.auto_select_seconds = 0.1
    mid = drv.drive_to("route_selected")
    drv.ok(mid, "route", {"route": "A"})
    time.sleep(0.25)
    m = client.get(f"/api/missions/{mid}").json()
    assert m["selectedRoute"] == "A" and m["selectionSource"] == "operator"
    assert [e["state"] for e in m["timeline"]].count("escorting") == 1


def test_payload(drv, ros):
    mid = drv.drive_to("escorting")
    assert drv.ok(mid, "payload", {"speaker": False}) == {"speaker": False, "led": True}
    assert ros.on(PAYLOAD_CMD)[-1] == {"missionId": mid, "simTimeS": None, "speaker": False, "led": None}
    assert drv.ok(mid, "payload", {"led": False}) == {"speaker": False, "led": False}


def test_abort_operator_and_return_to_dock(drv, ros):
    for reason in ("operator_abort", "return_to_dock"):
        mid = drv.drive_to("escorting")
        m = drv.ok(mid, "abort", {"reason": reason})
        assert m["state"] == "aborted" and m["abortReason"] == reason
        assert ros.on(MISSION_COMMAND)[-1]["command"] == "abort"
        drv.ok(mid, "close", {})


def test_arrived_result(drv, client):
    drv.drive_to("arrived")
    result = client.get("/api/missions/active").json()["result"]
    assert result["peopleWarned"] == 38  # final count on selected route B
    assert result["vsBestAlternativeS"] == 125.0 - 170.0  # vs fastest other route (C)
    assert result["droneBatteryPct"] == 100
    assert result["responseTimeS"] >= 0


def test_return_sends_reversed_polyline(drv, ros):
    mid = drv.drive_to("arrived")
    drv.on_loop(drv.svc.patient_loaded)
    m = drv.ok(mid, "return")
    assert m["state"] == "returning" and m["escort"]["leg"] == "return"
    cmd = ros.on(MISSION_COMMAND)[-1]
    assert cmd["command"] == "start_return" and cmd["route"] == "B"
    assert cmd["polylineLocalM"] == [[0.0, 112.0], [0.0, 0.0]]


# --- v1.4: rescue, First Aid Center, landing -------------------------------------


def test_arrival_starts_the_rescue(drv, client):
    drv.drive_to("arrived")
    assert client.get("/api/missions/active").json()["rescue"] == {"state": "in_progress", "patientLoadedAt": None}


def test_return_refused_until_the_patient_is_loaded(drv, ros):
    mid = drv.drive_to("arrived")
    sent = len(ros.on(MISSION_COMMAND))
    err = _envelope(drv.post(mid, "return", {}), 409, "invalid_transition")
    assert err["message"] == "Cannot return before the patient is loaded"
    assert err["details"] == {"state": "arrived", "action": "return", "rescue": "in_progress"}
    assert len(ros.on(MISSION_COMMAND)) == sent  # nothing sent to ROS
    m = drv.on_loop(drv.svc.patient_loaded)
    assert m.state is MissionState.ARRIVED and m.rescue.state == "patient_loaded" and m.rescue.patient_loaded_at
    assert drv.ok(mid, "return")["state"] == "returning"


def test_patient_loaded_is_broadcast_and_idempotent(drv, client):
    drv.drive_to("arrived")
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()  # snapshot
        first = drv.on_loop(drv.svc.patient_loaded).rescue.patient_loaded_at
        msg = ws.receive_json()
        assert msg["type"] == "mission.state"
        assert (msg["data"]["from"], msg["data"]["to"], msg["data"]["reason"]) == ("arrived", "arrived", "patient_loaded")
        assert drv.on_loop(drv.svc.patient_loaded).rescue.patient_loaded_at == first


@pytest.mark.parametrize("state", ["escorting", "returning"])
def test_patient_loaded_outside_arrived_is_rejected(drv, state):
    from app.state.machine import InvalidTransition

    drv.drive_to(state)
    with pytest.raises(InvalidTransition):
        drv.on_loop(drv.svc.patient_loaded)


def test_cart_at_hub_does_not_complete_the_mission(drv, client):
    """The cart at the First Aid Center leaves the mission returning; the landing completes it."""
    drv.drive_to("returning")
    m = drv.on_loop(drv.svc.cart_at_hub)
    assert m.state is MissionState.RETURNING and m.escort.progress == 1 and m.escort.eta_s == 0
    m = drv.on_loop(drv.svc.drone_landed)
    assert m.state is MissionState.COMPLETED
    timeline = [e["state"] for e in client.get("/api/missions/active").json()["timeline"]]
    assert timeline[-3:] == ["arrived", "returning", "completed"]


@pytest.mark.parametrize("state", ["escorting", "arrived", "aborted"])
def test_drone_landed_is_ignored_outside_returning(drv, state):
    drv.drive_to(state)
    assert drv.on_loop(drv.svc.drone_landed) is None
    assert drv.on_loop(drv.svc.cart_at_hub) is None
    assert drv.client.get("/api/missions/active").json()["state"] == state


def test_world_route_sends_the_world_paths(drv, ros):
    """For a route of the world, select_route / start_return carry routes.yaml's cart path
    and demo_common's return path to the First Aid Center."""
    from app.models import Route

    mid = drv.drive_to("scanning")
    p = drv.svc.local_position((0, 0))
    routes = [Route(id=r, label={"en": r}, length_m=100, people_count=0, count_final=True, est_time_s=t,
                    polyline=[p, p]) for r, t in (("A_west", 30.6), ("B_north", 29.0), ("C_east", 26.6))]
    drv.on_loop(drv.svc.route_recommended, routes, "B_north")
    drv.ok(mid, "route", {"route": "B_north"})
    assert ros.on(MISSION_COMMAND)[-1]["polylineLocalM"] == [[0.0, 0.0], [0.0, 98.0], [0.0, 112.0]]
    drv.on_loop(drv.svc.cart_at_incident)
    drv.on_loop(drv.svc.patient_loaded)
    drv.ok(mid, "return")
    assert ros.on(MISSION_COMMAND)[-1]["polylineLocalM"] == [[0.0, 107.0], [0.0, 98.0], [0.0, 0.0], [0.0, -15.0]]


def test_return_polyline_is_the_world_return_path(drv, client):
    """Mission.returnPolyline: World.return_path of the selected route, projected (§2), set on lock."""
    from app.models import Route

    mid = drv.drive_to("scanning")
    assert client.get("/api/missions/active").json()["returnPolyline"] is None
    p = drv.svc.local_position((0, 0))
    routes = [Route(id=r, label={"en": r}, length_m=100, people_count=0, count_final=True, est_time_s=t,
                    polyline=[p, p]) for r, t in (("A_west", 30.6), ("B_north", 29.0), ("C_east", 26.6))]
    drv.on_loop(drv.svc.route_recommended, routes, "C_east")
    assert client.get("/api/missions/active").json()["returnPolyline"] is None  # not before the lock
    m = drv.ok(mid, "route", {"route": "C_east"})
    # (5,112) (16,112) (36,92) (36,36) (0,0) (0,-15) -> x = 500 + 3.5 e, y = 526.5 - 3.5 n
    assert [(p["x"], p["y"]) for p in m["returnPolyline"]] == [
        (517.5, 134.5), (556.0, 134.5), (626.0, 204.5), (626.0, 400.5), (500.0, 526.5), (500.0, 579.0),
    ]
    assert all({"lat", "lon"} <= set(p) for p in m["returnPolyline"])


def test_history_carries_the_route_label(drv, client):
    from app.models import Route

    mid = drv.drive_to("scanning")
    p = drv.svc.local_position((0, 0))
    routes = [Route(id="C_east", label={"en": "C · East", "ar": "C · شرق"}, length_m=151.2, people_count=10, count_final=True,
                    est_time_s=26.6, polyline=[p, p])]
    drv.on_loop(drv.svc.route_recommended, routes, "C_east")
    drv.ok(mid, "route", {"route": "C_east"})
    drv.ok(mid, "abort", {"reason": "operator_abort"})
    drv.ok(mid, "close", {})
    item = client.get("/api/missions/history").json()[0]
    assert (item["route"], item["routeLabel"]) == ("C_east", {"en": "C · East", "ar": "C · شرق"})


# --- score derivation ---------------------------------------------------------------


def test_score_is_fastest_over_own_time(drv):
    """§3.7: score = fastest estTimeS / this route's estTimeS; the fastest scores 1.0."""
    drv.drive_to("scanning")
    m = drv.recommend("B")  # EST: A 218, B 125, C 170
    assert {r.id: r.score for r in m.routes} == {"A": round(125 / 218, 3), "B": 1.0, "C": round(125 / 170, 3)}
    assert all(r.crowd_factor is None for r in m.routes)


def test_score_is_null_until_est_time_arrives(drv):
    from app.service import with_scores

    drv.drive_to("scanning")
    partial = [r.model_copy(update={"est_time_s": None, "score": 0.7}) for r in make_routes()]
    drv.on_loop(drv.svc.route_scores, partial, False)
    assert [r["score"] for r in drv.client.get("/api/missions/active").json()["routes"]] == [None, None, None]
    mixed = with_scores([make_routes()[0], partial[1]])
    assert [r.score for r in mixed] == [1.0, None]


@pytest.mark.parametrize("outcome", ["resolved", "transferred"])
def test_close_completed(drv, client, outcome):
    mid = drv.drive_to("completed")
    m = drv.ok(mid, "close", {"outcome": outcome})
    assert m["state"] == "idle" and m["outcome"] == outcome
    states = [e["state"] for e in m["timeline"]]
    assert states == ["incident_received", "taking_off", "scanning", "route_selected",
                      "escorting", "arrived", "returning", "completed"]
    assert client.get(f"/api/incidents/{m['incidentId']}").json()["status"] == outcome
    [item] = client.get("/api/missions/history").json()
    assert item["route"] == "B" and item["routeLabel"] == {"en": "Route B", "ar": "المسار B"}
    assert item["droneId"] == "SBQ-01" and item["outcome"] == outcome


@pytest.mark.parametrize("state,outcome", [("aborted", "aborted"), ("error", "error")])
def test_close_aborted_and_error_ignore_body_outcome(drv, state, outcome):
    mid = drv.drive_to(state)
    assert drv.ok(mid, "close", {"outcome": "resolved"})["outcome"] == outcome


def test_close_without_body(drv, client):
    mid = drv.drive_to("aborted")
    assert client.post(f"/api/missions/{mid}/close").json()["outcome"] == "aborted"


def test_close_activates_next_queued(drv, client):
    first = drv.create_incident()
    second = drv.create_incident()
    assert second["status"] == "queued" and second["missionId"] is None
    drv.ok(first["missionId"], "dismiss")
    active = client.get("/api/missions/active").json()
    assert active["incidentId"] == second["id"] and active["state"] == "incident_received"
    assert client.get(f"/api/incidents/{second['id']}").json()["status"] == "active"


def test_battery_low_aborts(drv):
    drv.drive_to("escorting")
    m = drv.on_loop(drv.svc.battery_low)
    assert m.state.value == "aborted" and m.abort_reason == "low_battery"


# --- 409 on wrong state -------------------------------------------------------


WRONG_STATE_CASES = [
    (cmd.value, state)
    for cmd in Command
    for state in ALL_STATES
    if MissionState(state) not in COMMAND_STATES[cmd]
]


@pytest.mark.parametrize("action,state", WRONG_STATE_CASES, ids=lambda v: str(v))
def test_409_on_wrong_state(drv, action, state):
    mid = drv.drive_to(state)
    err = _envelope(drv.post(mid, action, BODIES[action]), 409, "invalid_transition")
    assert err["details"] == {"state": state, "action": action}


@pytest.mark.parametrize("action", list(BODIES))
def test_409_on_closed_mission(drv, action):
    mid = drv.drive_to("incident_received")
    drv.ok(mid, "dismiss")
    _envelope(drv.post(mid, action, BODIES[action]), 409, "invalid_transition")


@pytest.mark.parametrize("action", list(BODIES))
def test_404_on_unknown_mission(drv, action):
    _envelope(drv.post("MSN-9999", action, BODIES[action]), 404, "not_found")


# --- 400 on bad input ---------------------------------------------------------


@pytest.mark.parametrize(
    "state,action,body",
    [
        ("incident_received", "launch", {"unexpected": 1}),
        ("incident_received", "dismiss", {"reason": "false_alarm"}),
        ("route_selected", "route", {}),
        ("route_selected", "route", {"route": ""}),
        ("escorting", "payload", {}),
        ("escorting", "payload", {"speaker": "loud"}),
        ("escorting", "abort", {}),
        ("escorting", "abort", {"reason": "low_battery"}),  # backend-internal reason
        ("arrived", "return", {"now": True}),
        ("completed", "close", {}),
        ("completed", "close", {"outcome": "aborted"}),
    ],
)
def test_400_on_bad_body(drv, state, action, body):
    mid = drv.drive_to(state)
    _envelope(drv.post(mid, action, body), 400, "bad_request")
    assert drv.client.get("/api/missions/active").json()["state"] == state  # unchanged
