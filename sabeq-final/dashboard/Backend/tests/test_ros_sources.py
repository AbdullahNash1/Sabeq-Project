"""ROS sources on the fake ROS graph: converters fed with the exact messages the real nodes
build, the ack gate, command publishing, MJPEG, detection rate, heartbeat, Node 2's rule."""
import asyncio
from functools import partial
import json
import time

from fastapi.testclient import TestClient
import pytest

from app.config import Settings
from app.main import create_app
from app.ros import converters as conv
from app.ros import topics as T
from app.ros.fake import CompressedImage, Detection2DArray, FakeRosGraph, FakeTransport, Header, Image, MSG_TYPES, Time
from app.ros.wire import BEST_EFFORT_5, ImageFrame, RELIABLE_10, RELIABLE_20, from_wire
from app.sources.commands import MissionCommand, PayloadCommand
from app.sources.factory import build_sources
from app.sources.ros.camera import encode_jpeg
from tests.conftest import INCIDENT_BODY
from tests.rosim import AVAILABLE, ROUTES_YAML, WORLD_META, sabeq_mission

ROS = dict(source_camera="ros", source_route="ros", source_drone="ros", source_cart="ros")
needs_pkg = pytest.mark.skipif(not AVAILABLE, reason="ros_ws/src/sabeq_mission not in checkout")


def make(**overrides):
    graph = FakeRosGraph()
    settings = Settings(_env_file=None, **{**dict(
        auto_select_seconds=60, command_ack_timeout_s=0.3, system_status_interval_s=3600,
        heartbeat_timeout_s=1.0), **ROS, **overrides})
    app = create_app(settings, sources_factory=lambda s, svc, st: build_sources(
        s, svc, st, ros_transport=FakeTransport(graph)), record_commands=True)
    return app, graph


def pub_json(graph, node, topic, qos, payload):
    graph.publish(node, topic, qos, MSG_TYPES.String(json.dumps(payload)))


def dock(client, app):
    client.portal.call(partial(
        app.state.service.drone_telemetry, state="docked", battery_pct=100,
        position=app.state.store.drone.position, altitude_m=0, heading_deg=0, speed_mps=0,
        docked_on_cart=True, link="ok"))


def settle(client, seconds=0.05):
    client.portal.call(asyncio.sleep, seconds)


# --- converters, with the real nodes' messages ------------------------------------------------

@needs_pkg
def test_mission_node_messages_convert():
    sabeq_mission()
    import sabeq_mission.messages as mm
    status = mm.drone_status(
        drone_id="SBQ-01", sim_time_s=12.5, mission_id="MSN-0001", state="scanning", armed=True,
        docked_on_cart=False, battery_pct=97.3, link="ok", px4_mode="OFFBOARD",
        payload_state={"speaker": True, "led": False}, camera_mode="scan_360",
        scan=mm.scan_info("rotate", 1 / 3, "B_north"), fault=None)
    st = conv.drone_status(json.loads(json.dumps(status)))
    assert st == {"mission_id": "MSN-0001", "state": "scanning", "docked": False,
                  "battery_pct": 97.3, "flight_time_left_s": 1518, "link": "ok",
                  "camera_mode": "scan_360", "payload": (True, False),
                  "scan": {"phase": "rotate", "progress": 0.333, "route": "B_north",
                           "coveragePct": 33.3}, "fault": None}
    pos = mm.drone_position(drone_id="SBQ-01", sim_time_s=1, e=36.0, n=52.4, altitude_m=8.04,
                            heading_deg=359.98, speed_mps=10.0)
    assert conv.drone_position(pos) == {"local": (36.0, 52.4), "altitude_m": 8.04,
                                        "heading_deg": 0.0, "speed_mps": 10.0}


@needs_pkg
def test_world_node_messages_convert():
    sm = sabeq_mission()
    world = sm.world.load_world(str(WORLD_META), str(ROUTES_YAML))
    logic = sm.world_logic.WorldLogic(world, lambda *a: None, lambda: 3.0,
                                      sm.pose_sink.FakePoseSink(), sm.audio.FakeAudio())
    logic.on_command({"commandId": "c", "missionId": "M", "command": "launch"})
    logic.on_command({"commandId": "c2", "missionId": "M", "command": "select_route",
                      "route": "C_east"})
    st = conv.cart_status(json.loads(json.dumps(logic.cart_status_message())))
    assert st == {"mission_id": "M", "state": "to_incident", "ret_phase": None,
                  "rescue_progress": "none", "route": "C_east", "leg": "outbound"}
    pose = conv.cart_pose(logic.cart_pose_message())
    assert pose == {"local": (0.0, 0.0), "heading_deg": 0.0, "speed_kmh": 0.0}


def test_node2_route_scores_convert_in_world_order():
    """autonomy_node publishes json.dumps({route: {distanceM, peopleCount, estimatedTimeS}})."""
    app, _ = make()
    svc = app.state.service
    scores = json.loads(json.dumps({
        "C_east": {"distanceM": 151.2, "peopleCount": 10, "estimatedTimeS": 26.6},
        "A_west": {"distanceM": 151.2, "peopleCount": 26, "estimatedTimeS": 34.6},
        "B_north": {"distanceM": 112.0, "peopleCount": 26, "estimatedTimeS": 29.0},
        "Z_unknown": {"distanceM": 1.0, "peopleCount": 0, "estimatedTimeS": 0.1}}))
    routes = conv.scored_routes(svc.world, scores, svc.local_position)
    assert [r.id for r in routes] == ["A_west", "B_north", "C_east"]
    c = routes[2]
    assert (c.length_m, c.people_count, c.est_time_s, c.count_final, c.crowd_factor) == \
        (151.2, 10, 26.6, True, None)
    assert c.label.en == "C · East" and (c.polyline[0].x, c.polyline[0].y) == (500.0, 526.5)


def test_wire_image_frame_and_jpeg():
    w, h = 4, 2
    pixels = bytes([255, 0, 0]) * (w * h)
    frame = from_wire("image", Image(header=Header(stamp=Time(44, 784000000)), height=h, width=w,
                                     encoding="rgb8", step=w * 3, data=pixels))
    assert (frame.width, frame.height, frame.sim_time_s) == (4, 2, 44.784)
    from io import BytesIO
    from PIL import Image as PILImage
    for enc, want in (("rgb8", (255, 0, 0)), ("bgr8", (0, 0, 255))):
        jpeg = encode_jpeg(ImageFrame(w, h, enc, w * 3, pixels, None))
        px = PILImage.open(BytesIO(jpeg)).convert("RGB").getpixel((1, 1))
        assert all(abs(a - b) < 40 for a, b in zip(px, want)), (enc, px)


# --- ack gate and command publishing ------------------------------------------------------------

@pytest.mark.parametrize("parts, launch, reset, payload", [
    (ROS, ["mission_node", "world_node"], ["world_node"], []),
    ({**ROS, "source_cart": "mock"}, ["mission_node"], [], []),
    ({**ROS, "source_drone": "mock"}, ["world_node"], ["world_node"], []),
    ({"source_camera": "ros", "source_route": "mock", "source_drone": "mock",
      "source_cart": "mock"}, [], [], []),
])
def test_who_must_acknowledge(parts, launch, reset, payload):
    app, _ = make(**parts)
    with TestClient(app):
        ctx = app.state.sources.ros
        assert ctx.expected_acks(MissionCommand("M", "launch")) == launch
        assert ctx.expected_acks(MissionCommand(None, "reset")) == reset
        assert ctx.expected_acks(PayloadCommand("M", speaker=False)) == payload


def test_commands_are_published_once_in_the_nodes_format():
    app, graph = make()
    with TestClient(app) as c:
        mid = c.post("/api/incidents", json=INCIDENT_BODY).json()["missionId"]
        settle(c)
        incidents = graph.on(T.INCIDENT)
        assert len(incidents) == 1 and json.loads(incidents[0].data)["missionId"] == mid
        assert graph.publishers[("backend_bridge", T.INCIDENT)].transient_local
        dock(c, app)
        r = c.post(f"/api/missions/{mid}/launch", json={})        # nobody answers
        assert r.status_code == 504 and r.json()["error"]["code"] == "ros_timeout"
        assert r.json()["error"]["details"]["nodes"] == ["mission_node", "world_node"]
        cmds = [json.loads(m.data) for m in graph.on(T.MISSION_COMMAND)]
        assert len(cmds) == 1 and cmds[0]["command"] == "launch"
        assert c.get("/api/missions/active").json()["state"] == "incident_received"


def test_ack_rejection_and_acceptance():
    app, graph = make()
    graph_events = []

    def mission_node_acks(msg):
        cmd = json.loads(msg.data)
        graph_events.append(cmd["command"])
        for node, ok, reason in (("mission_node", True, None), ("world_node", cmd["route"] != "A_west",
                                                                 "invalid_state")):
            pub_json(graph, node, T.MISSION_EVENTS, RELIABLE_20, {
                "missionId": cmd["missionId"], "simTimeS": 1.0, "source": node,
                "event": "command_ack", "details": {"commandId": cmd["commandId"],
                                                     "accepted": ok, "reason": reason}})
    with TestClient(app) as c:
        graph.subscribe("fake_nodes", T.MISSION_COMMAND, RELIABLE_20, mission_node_acks)
        mid = c.post("/api/incidents", json=INCIDENT_BODY).json()["missionId"]
        svc = app.state.service
        dock(c, app)
        assert c.post(f"/api/missions/{mid}/launch", json={}).json()["state"] == "taking_off"
        c.portal.call(svc.scan_altitude_reached)
        from app.ros.converters import live_routes
        routes = live_routes(svc.world, {}, svc.local_position)
        c.portal.call(svc.route_scores, routes, False)
        c.portal.call(svc.route_recommended, routes, "C_east")
        bad = c.post(f"/api/missions/{mid}/route", json={"route": "A_west"})
        assert bad.status_code == 409 and bad.json()["error"]["details"] == {
            "command": "select_route", "node": "world_node", "reason": "invalid_state"}
        assert c.get("/api/missions/active").json()["state"] == "route_selected"
        ok = c.post(f"/api/missions/{mid}/route", json={"route": "B_north"})
        assert ok.status_code == 200 and ok.json()["state"] == "escorting"


def test_background_command_failure_puts_the_mission_in_error():
    """The countdown's select_route is not acknowledged: the mission goes to error."""
    app, graph = make(auto_select_seconds=0.05)
    with TestClient(app) as c, c.websocket_connect("/ws") as ws:
        ws.receive_json()
        mid = c.post("/api/incidents", json=INCIDENT_BODY).json()["missionId"]
        svc = app.state.service

        def ack_launch(msg):
            cmd = json.loads(msg.data)
            if cmd["command"] == "launch":
                for node in ("mission_node", "world_node"):
                    pub_json(graph, node, T.MISSION_EVENTS, RELIABLE_20, {
                        "missionId": mid, "simTimeS": 0, "source": node, "event": "command_ack",
                        "details": {"commandId": cmd["commandId"], "accepted": True,
                                    "reason": None}})
        graph.subscribe("fake_nodes", T.MISSION_COMMAND, RELIABLE_20, ack_launch)
        dock(c, app)
        assert c.post(f"/api/missions/{mid}/launch", json={}).status_code == 200
        c.portal.call(svc.scan_altitude_reached)
        from app.ros.converters import live_routes
        routes = live_routes(svc.world, {}, svc.local_position)
        c.portal.call(svc.route_recommended, routes, "C_east")
        deadline = time.monotonic() + 3
        while c.get("/api/missions/active").json()["state"] != "error":
            assert time.monotonic() < deadline
            time.sleep(0.02)
        m = c.get("/api/missions/active").json()
        assert m["error"]["code"] == "command_timeout" and "select_route" in m["error"]["message"]
        for _ in range(1000):
            msg = ws.receive_json()
            if msg["type"] == "error":
                assert msg["data"]["code"] == "command_timeout"
                break


# --- sources -------------------------------------------------------------------------------------

def test_camera_mjpeg_is_encoded_once_per_frame(monkeypatch):
    calls = []
    import app.sources.ros.camera as cam
    real = cam.encode_jpeg
    monkeypatch.setattr(cam, "encode_jpeg", lambda f: calls.append(f.seq) or real(f))
    app, graph = make()
    with TestClient(app) as c:
        w, h = 32, 24
        graph.publish("ros_gz_bridge", T.CAMERA_IMAGE, BEST_EFFORT_5.__class__(False, 1), Image(
            header=Header(), height=h, width=w, encoding="rgb8", step=w * 3,
            data=bytes(w * h * 3)))
        settle(c)
        camera = app.state.sources.camera
        assert c.get("/api/drones/SBQ-01/camera").json()["resolution"] == {"w": 32, "h": 24}
        a = c.portal.call(camera.latest_jpeg)
        b = c.portal.call(camera.latest_jpeg)
        assert a is b and a[:2] == b"\xff\xd8" and len(calls) == 1


def _jpeg(w, h):
    from io import BytesIO
    from PIL import Image as PILImage
    buf = BytesIO()
    PILImage.new("RGB", (w, h), (0, 128, 255)).save(buf, format="JPEG")
    return buf.getvalue()


def _publish_jpeg(graph, w, h):
    data = _jpeg(w, h)
    graph.publish("image_republisher", T.CAMERA_COMPRESSED, BEST_EFFORT_5.__class__(False, 1),
                  CompressedImage(header=Header(), format="rgb8; jpeg compressed bgr8", data=data))
    return data


def _publish_raw(graph, w, h):
    graph.publish("ros_gz_bridge", T.CAMERA_IMAGE, BEST_EFFORT_5.__class__(False, 1), Image(
        header=Header(), height=h, width=w, encoding="rgb8", step=w * 3, data=bytes(w * h * 3)))


def test_wire_compressed_frame_streams_the_jpeg_as_is():
    data = _jpeg(40, 30)
    frame = from_wire("jpeg", CompressedImage(header=Header(stamp=Time(3)), format="jpeg",
                                              data=data))
    assert (frame.width, frame.height, frame.encoding, frame.sim_time_s) == (40, 30, "jpeg", 3.0)
    assert encode_jpeg(frame) is frame.data
    assert from_wire("jpeg", CompressedImage(header=Header(), format="png", data=b"x")) is None


@pytest.mark.parametrize("transport", ["compressed", "auto"])
def test_camera_prefers_compressed_frames(transport):
    app, graph = make(camera_transport=transport)
    with TestClient(app) as c:
        data = _publish_jpeg(graph, 40, 30)
        settle(c)
        _publish_raw(graph, 32, 24)       # ignored: no raw subscription / JPEG is flowing
        settle(c)
        assert c.get("/api/drones/SBQ-01/camera").json()["resolution"] == {"w": 40, "h": 30}
        assert c.portal.call(app.state.sources.camera.latest_jpeg) == data


def test_auto_falls_back_to_raw_without_compressed_frames(monkeypatch):
    import app.sources.ros.camera as cam
    monkeypatch.setattr(cam, "RAW_FALLBACK_S", 0.5)
    app, graph = make(camera_transport="auto")
    with TestClient(app) as c:
        _publish_raw(graph, 32, 24)       # before the fallback: nobody listens to raw yet
        settle(c)
        assert not c.get("/api/drones/SBQ-01/camera").json()["available"]
        settle(c, 1.6)
        _publish_raw(graph, 32, 24)
        settle(c)
        assert c.get("/api/drones/SBQ-01/camera").json()["resolution"] == {"w": 32, "h": 24}


def test_detection_rate_and_ros_chip_from_the_camera():
    app, graph = make(source_drone="mock", source_cart="mock", source_route="mock")
    with TestClient(app) as c:
        for i in range(12):
            graph.publish("yolo_node", T.PERSON_DETECTIONS, RELIABLE_10.__class__(True, 10),
                          Detection2DArray(header=Header(stamp=Time(i)), detections=[]))
            graph.publish("ros_gz_bridge", T.CAMERA_IMAGE, BEST_EFFORT_5.__class__(False, 1), Image(
                header=Header(), height=2, width=2, encoding="rgb8", step=6, data=bytes(12)))
            settle(c, 0.1)
        settle(c, 1.1)
        system = c.get("/api/system/status").json()
        assert 5 < system["jetson"]["fps"] < 15 and system["jetson"]["ok"]
        assert system["ros"] == {"ok": True, "label": "OK"}


def test_reliable_detections_reach_the_best_effort_backend_subscription():
    """yolo_node publishes RELIABLE depth 10; the backend reads BEST_EFFORT depth 5 (compatible)."""
    app, graph = make(source_drone="mock", source_cart="mock", source_route="mock")
    with TestClient(app) as c:
        with c.websocket_connect("/ws") as ws:
            ws.receive_json()
            c.post("/api/incidents", json=INCIDENT_BODY)
            graph.publish("yolo_node", T.PERSON_DETECTIONS, RELIABLE_10, Detection2DArray(
                header=Header(stamp=Time(7, 5)), detections=[]))
            for _ in range(200):
                msg = ws.receive_json()
                if msg["type"] == "camera.detections":
                    assert msg["data"]["simTimeS"] == pytest.approx(7.000000005)
                    break


def test_drone_heartbeat_link_lost():
    app, graph = make()
    with TestClient(app) as c:
        mid = c.post("/api/incidents", json=INCIDENT_BODY).json()["missionId"]
        status = {"droneId": "SBQ-01", "simTimeS": 1, "missionId": None, "state": "docked",
                  "armed": False, "dockedOnCart": True, "batteryPct": 100, "flightTimeLeftS": 1560,
                  "link": "ok", "px4Mode": "HOLD", "payload": {}, "payloadState": {
                      "speaker": True, "led": True}, "cameraMode": "off", "scan": None, "fault": None}
        pub_json(graph, "mission_node", T.DRONE_STATUS, RELIABLE_10, status)
        settle(c)
        assert c.get("/api/drones/SBQ-01").json()["link"] == "ok"
        assert c.get("/api/system/status").json()["ros"]["ok"] is True
        settle(c, 2.2)                     # heartbeat_timeout_s = 1
        assert c.get("/api/drones/SBQ-01").json()["link"] == "lost"
        assert c.get("/api/system/status").json()["ros"]["ok"] is False
        assert c.get(f"/api/missions/{mid}").json()["state"] == "error"


def test_live_counts_follow_node_2s_rule():
    app, graph = make()
    with TestClient(app) as c:
        c.post("/api/incidents", json=INCIDENT_BODY)
        svc = app.state.service
        dock(c, app)
        app.state.store.active_mission.state = app.state.store.active_mission.state.__class__(
            "scanning")
        route = app.state.sources.route

        def frames(n):
            graph.publish("yolo_node", T.PERSON_DETECTIONS, RELIABLE_10, Detection2DArray(
                header=Header(), detections=[object()] * n))
        graph.publish("mission_node", T.SCAN_ACTIVE_ROUTE, RELIABLE_10, MSG_TYPES.String("A_west"))
        frames(5)
        frames(18)
        frames(3)
        graph.publish("mission_node", T.SCAN_ACTIVE_ROUTE, RELIABLE_10, MSG_TYPES.String("B_north"))
        frames(26)
        settle(c)
        assert route.counts == {"A_west": 18, "B_north": 26, "C_east": 0}
        m = c.get("/api/missions/active").json()
        assert {r["id"]: r["peopleCount"] for r in m["routes"]}["A_west"] == 18
