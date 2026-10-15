"""End-to-end: the backend with every SOURCE_*=ros on the fake ROS graph, wired to the REAL
mission_node / world_node logic (sabeq_mission) and fakes of Node 1 / Node 2, driven through
REST and the WebSocket exactly as the dashboard does."""
import time

from fastapi.testclient import TestClient
import pytest

from app.config import Settings
from app.main import create_app
from app.ros.fake import FakeRosGraph, FakeTransport
from app.sources.factory import build_sources
from tests.conftest import INCIDENT_BODY
from tests.rosim import AVAILABLE, RosWorldSim

pytestmark = pytest.mark.skipif(not AVAILABLE, reason="ros_ws/src/sabeq_mission not in checkout")

ROS = dict(source_camera="ros", source_route="ros", source_drone="ros", source_cart="ros")


def ros_app(**overrides):
    graph = FakeRosGraph()
    settings = Settings(_env_file=None, **{**dict(
        auto_select_seconds=0.3, command_ack_timeout_s=1.0, dev_mode=True,
        system_status_interval_s=3600, ws_queue_size=100_000), **ROS, **overrides})
    app = create_app(settings, sources_factory=lambda s, svc, st: build_sources(
        s, svc, st, ros_transport=FakeTransport(graph)))
    return app, graph


class Run:
    """A TestClient + the ROS world sim running inside the app's loop."""

    def __init__(self, app, graph, **sim_kw):
        self.app, self.graph = app, graph
        self.client = TestClient(app)
        self.client.__enter__()
        self.sim = RosWorldSim(graph, **sim_kw)
        self.client.portal.start_task_soon(self.sim.run)

    def close(self):
        self.sim.running = False
        time.sleep(0.05)
        self.client.__exit__(None, None, None)

    def mission(self):
        return self.client.get("/api/missions/active").json()

    def wait(self, pred, what, timeout=30.0):
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            last = self.mission()
            if pred(last):
                return last
            time.sleep(0.02)
        raise AssertionError(f"timed out waiting for {what}; mission={last and last['state']}, "
                             f"mission_node={self.sim.mission.phase.value}, "
                             f"cart={self.sim.world_logic.cart_state()}, sim t={self.sim.t}, "
                             f"sim error={self.sim.error!r}")

    def wait_state(self, state, timeout=30.0):
        return self.wait(lambda m: m is not None and m["state"] == state, state, timeout)

    def drone(self):
        return self.client.get("/api/drones/SBQ-01").json()

    def post(self, mission_id, action, body=None):
        return self.client.post(f"/api/missions/{mission_id}/{action}", json=body or {})

    def start(self):
        deadline = time.monotonic() + 10
        while not (self.drone()["dockedOnCart"] and self.drone()["link"] == "ok"):
            assert time.monotonic() < deadline, f"drone never docked: {self.drone()}"
            time.sleep(0.02)
        mid = self.client.post("/api/incidents", json=INCIDENT_BODY).json()["missionId"]
        r = self.post(mid, "launch")
        assert r.status_code == 200, r.text
        return mid


@pytest.fixture
def run():
    runs = []

    def make(**kw):
        sim_kw = {k: kw.pop(k) for k in ("mission_params", "flight", "world_kw") if k in kw}
        app, graph = ros_app(**kw)
        r = Run(app, graph, **sim_kw)
        runs.append(r)
        return r
    yield make
    for r in runs:
        r.close()


def test_full_mission_through_rest_and_ws(run):
    r = run()
    with r.client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "snapshot"
        mid = r.start()
        launched = r.mission()
        assert launched["state"] == "taking_off"           # after both nodes acked launch
        r.wait_state("scanning")
        m = r.wait(lambda m: m["state"] in ("route_selected", "escorting"), "recommendation")
        assert m["recommendedRoute"] == "C_east"
        people = {x["id"]: x["peopleCount"] for x in m["routes"]}
        # Node 2 counts by arrival: frames that arrive while the drone turns from A_west to
        # B_north still count for A_west (ROS_TOPICS doc/code differences) - A_west may show
        # B_north's crowd; B_north and C_east are exact (a later route is never inflated).
        assert people["A_west"] in (18, 26) and people["B_north"] == 26 and people["C_east"] == 10
        for x in m["routes"]:
            assert x["estTimeS"] == round(x["lengthM"] / 7.0 + x["peopleCount"] * 0.5, 2)
        fastest = min(x["estTimeS"] for x in m["routes"])
        assert {x["id"]: x["score"] for x in m["routes"]} == {
            x["id"]: round(fastest / x["estTimeS"], 3) for x in m["routes"]}
        assert next(x for x in m["routes"] if x["id"] == "C_east")["score"] == 1.0
        m = r.wait_state("escorting")
        assert m["selectedRoute"] == "C_east" and m["selectionSource"] == "auto"
        m = r.wait_state("arrived", timeout=60)
        assert m["rescue"]["state"] == "in_progress"
        early = r.post(mid, "return")
        assert early.status_code == 409 and early.json()["error"]["code"] == "invalid_transition"
        m = r.wait(lambda m: m["rescue"]["state"] == "patient_loaded", "patient loaded", 60)
        ok = r.post(mid, "return")
        assert ok.status_code == 200 and ok.json()["state"] == "returning"
        r.wait(lambda m: m["state"] in ("returning", "completed") and r.client.get(
            "/api/carts/CART-01").json()["state"] == "at_hub", "cart at the First Aid Center", 60)
        m = r.wait_state("completed", timeout=60)
        drone = r.drone()
        assert drone["dockedOnCart"] and drone["state"] == "docked"
        cart = r.client.get("/api/carts/CART-01").json()
        assert (cart["position"]["x"], cart["position"]["y"]) == (500.0, 579.0)  # FA stop (0,-15)
        closed = r.post(mid, "close", {"outcome": "resolved"}).json()
        assert closed["state"] == "idle"
        messages = []
        for _ in range(100_000):
            msg = ws.receive_json()
            messages.append(msg)
            if msg["type"] == "mission.history":
                break
    types = {m["type"] for m in messages}
    assert {"route.scores", "route.recommendation", "route.selected", "camera.detections",
            "drone.telemetry", "cart.telemetry", "scan.progress", "escort.progress",
            "system.status"} <= types
    live = [m["data"] for m in messages if m["type"] == "route.scores" and not m["data"]["final"]]
    assert live and any(sum(x["peopleCount"] for x in d["routes"]) > 0 for d in live)
    status = [m["data"] for m in messages if m["type"] == "system.status"]
    assert any(s["ros"]["ok"] for s in status) and any(s["jetson"]["fps"] > 0 for s in status)
    timeline = [e["state"] for e in closed["timeline"]]
    assert timeline == ["incident_received", "taking_off", "scanning", "route_selected",
                        "escorting", "arrived", "returning", "completed"]
    # the commands went out once each, in mission_node's / world_node's format
    cmds = [m for m in r.graph.on("/mission/command")]
    import json
    names = [json.loads(m.data)["command"] for m in cmds]
    assert names == ["launch", "select_route", "start_return"]
    first = json.loads(cmds[0].data)
    assert set(first) == {"commandId", "missionId", "simTimeS", "command", "route", "source",
                          "polylineLocalM", "scanAltitudeM", "reason"}
    assert first["missionId"] == mid and first["scanAltitudeM"] == 8.0
    assert isinstance(first["simTimeS"], float)


def test_operator_override_during_countdown(run):
    r = run(auto_select_seconds=30)
    mid = r.start()
    r.wait_state("route_selected")
    m = r.post(mid, "route", {"route": "A_west"})
    assert m.status_code == 200 and m.json()["state"] == "escorting"
    assert m.json()["selectedRoute"] == "A_west" and m.json()["selectionSource"] == "operator"
    r.wait(lambda _: r.sim.world_logic.route == "A_west", "world_node drives A_west", 5)
    r.wait(lambda _: r.sim.mission.route is not None and r.sim.mission.route.name == "A_west",
           "mission_node escorts A_west", 5)


@pytest.mark.parametrize("phase", ["scanning", "escorting", "returning"])
def test_abort_in_several_phases(run, phase):
    r = run()
    mid = r.start()
    if phase == "returning":
        r.wait(lambda m: m["state"] == "arrived" and m["rescue"]["state"] == "patient_loaded",
               "patient loaded", 60)
        assert r.post(mid, "return").status_code == 200
        r.wait(lambda _: r.sim.world_logic.ret_s > 20, "on the way back", 30)
    else:
        r.wait_state(phase, timeout=60)
    resp = r.post(mid, "abort", {"reason": "operator_abort"})
    assert resp.status_code == 200 and resp.json()["state"] == "aborted"
    deadline = time.monotonic() + 30
    while not r.drone()["dockedOnCart"]:
        assert time.monotonic() < deadline, r.drone()
        time.sleep(0.05)
    if phase != "scanning":
        assert r.client.get("/api/carts/CART-01").json()["state"] == "stopped"
    assert r.post(mid, "close").json()["outcome"] == "aborted"


def test_start_return_rejected_by_world_node(run):
    """The backend believes the patient is loaded (a race), world_node does not: 409 with
    world_node's reason, shown as a toast; the mission stays arrived."""
    r = run()
    mid = r.start()
    r.wait(lambda m: m["state"] == "arrived" and m["rescue"]["state"] == "in_progress",
           "arrived, rescue running", 60)
    r.client.portal.call(r.app.state.service.patient_loaded)
    resp = r.post(mid, "return")
    assert resp.status_code == 409
    err = resp.json()["error"]
    assert err["code"] == "command_rejected"
    assert err["details"] == {"command": "start_return", "node": "world_node",
                              "reason": "patient_not_loaded"}
    assert "world_node rejected start_return: patient_not_loaded" == err["message"]
    assert r.mission()["state"] == "arrived"


def test_missing_ack_is_a_ros_timeout(run):
    r = run()
    mid = r.start()
    r.wait_state("escorting", timeout=60)
    r.sim.world_node.deaf = True
    t0 = time.monotonic()
    resp = r.post(mid, "abort", {"reason": "operator_abort"})
    waited = time.monotonic() - t0
    assert resp.status_code == 504
    err = resp.json()["error"]
    assert err["code"] == "ros_timeout" and err["details"]["nodes"] == ["world_node"]
    assert 0.9 < waited < 3.0                                  # command_ack_timeout_s = 1
    assert r.mission()["state"] == "escorting"                 # nothing changed


def test_node_error_puts_the_mission_in_error(run):
    r = run()
    r.start()
    r.wait_state("escorting", timeout=60)
    with r.client.websocket_connect("/ws") as ws:
        ws.receive_json()
        r.client.portal.call(r.sim.flight.disarm)               # mission_node: fault disarmed
        m = r.wait_state("error", timeout=10)
        assert m["error"]["code"] == "disarmed"
        for _ in range(10_000):
            msg = ws.receive_json()
            if msg["type"] == "error":
                assert msg["data"]["code"] == "disarmed"
                break


def test_reset_sim_resets_world_node(run):
    r = run()
    r.start()
    r.wait_state("escorting", timeout=60)
    r.wait(lambda _: r.sim.world_logic.cart_s > 10, "cart moving", 30)
    snap = r.client.post("/api/dev/sim/reset").json()
    assert snap["activeMission"] is None
    r.wait(lambda _: "reset" in r.sim.world_commands and r.sim.world_logic.route is None,
           "world_node reset", 5)
    assert r.sim.world_logic.cart == tuple(r.sim.world.meta["cart_start_enu"])
    import json
    resets = [json.loads(m.data) for m in r.graph.on("/mission/command")
              if json.loads(m.data)["command"] == "reset"]
    assert len(resets) == 1 and resets[0]["missionId"] is None


def test_ros_qos_matches_the_nodes(run):
    """Every backend subscription receives what the real nodes publish (DDS QoS rules)."""
    r = run()
    r.start()
    r.wait_state("scanning", timeout=30)
    transport = r.app.state.sources.ros.transport
    subs = {t: q for t, lst in transport.subscriptions.items() for _, q in lst}
    pubs = {t: q for (owner, t), q in r.graph.publishers.items() if owner != "backend_bridge"}
    for topic, qos in subs.items():
        if topic in pubs:
            assert not (qos.reliable and not pubs[topic].reliable), topic
    assert not subs["/camera/image"].reliable and subs["/camera/image"].depth == 1
    assert not subs["/drone/position"].reliable and not subs["/cart/pose"].reliable
