"""The ROS side of the end-to-end co-simulation, on app.ros.fake.FakeRosGraph (no ROS needed).

Participants, with the QoS their real code uses:
- mission_node: the REAL sabeq_mission MissionLogic + FakeFlight (ros_io.py QoS);
- world_node:   the REAL sabeq_mission WorldLogic + FakePoseSink + FakeAudio (world_node.py QoS);
- autonomy_node (Node 2): FakeAutonomyNode, a line-by-line mirror of sabeq_ros autonomy_node.py;
- yolo_node (Node 1): FakeYolo, /person_detections (+ a small /camera/image) where the drone
  looks: the scan-zone people of the route it faces.
Everything runs on one sim clock, stepped inside the app's event loop by RosWorldSim.run().
"""
import asyncio
import json
import math
from pathlib import Path
import sys
from types import SimpleNamespace

from app.ros import topics as T
from app.ros.fake import Detection2DArray, Header, Image, MSG_TYPES, Time
from app.ros.wire import BEST_EFFORT_1, BEST_EFFORT_5, from_wire, LATCHED_1, QoS, RELIABLE_10, \
    RELIABLE_20, to_wire

ROOT = Path(__file__).resolve().parents[2]
PKG = ROOT / "ros_ws" / "src" / "sabeq_mission"
WORLD_META = ROOT / "docs" / "team" / "world_meta.json"
ROUTES_YAML = ROOT / "ros_ws" / "src" / "sabeq_ros" / "config" / "routes.yaml"
AVAILABLE = (PKG / "sabeq_mission" / "mission_logic.py").exists() and WORLD_META.exists()


def sabeq_mission():
    """Import the ROS package's pure modules (no rclpy needed) without writing bytecode."""
    sys.dont_write_bytecode = True
    if str(PKG) not in sys.path:
        sys.path.insert(0, str(PKG))
    import sabeq_mission.audio as audio
    import sabeq_mission.flight as flight
    import sabeq_mission.mission_logic as mission_logic
    import sabeq_mission.pose_sink as pose_sink
    import sabeq_mission.world as world
    import sabeq_mission.world_logic as world_logic
    return SimpleNamespace(audio=audio, flight=flight, mission_logic=mission_logic,
                           pose_sink=pose_sink, world=world, world_logic=world_logic)


# QoS exactly as the node code sets it
MISSION_NODE_PUB = {   # sabeq_mission/ros_io.py
    T.SCAN_ACTIVE_ROUTE: ("string", RELIABLE_10), T.SCAN_COMPLETE: ("bool", RELIABLE_10),
    T.DRONE_STATUS: ("json", RELIABLE_10), T.DRONE_POSITION: ("json", BEST_EFFORT_5),
    T.MISSION_EVENTS: ("json", RELIABLE_20), T.MISSION_COMMAND: ("json", RELIABLE_20),
}
WORLD_NODE_PUB = {     # sabeq_mission/world_node.py
    T.CART_POSE: ("json", BEST_EFFORT_5), T.CART_STATUS: ("json", RELIABLE_10),
    T.MISSION_EVENTS: ("json", RELIABLE_20),
}
NODE2_QOS = QoS(True, 10)          # autonomy_node.py: plain depth 10 (RELIABLE, VOLATILE)
YOLO_DETECTIONS_QOS = QoS(True, 10)  # yolo_node.py: depth 10
CAMERA_QOS = QoS(False, 1)         # ros_gz_bridge camera, best effort


class _Participant:

    def __init__(self, graph, name):
        self.graph, self.name = graph, name
        self.deaf = False    # tests: stop reacting to commands (missing-ack scenario)

    def sub(self, topic, kind, qos, fn):
        def on_message(msg):
            value = from_wire(kind, msg)
            if value is not None:
                fn(value)
        self.graph.subscribe(self.name, topic, qos, on_message)

    def pub(self, topic, kind, qos, payload):
        self.graph.publish(self.name, topic, qos, to_wire(kind, payload, MSG_TYPES))


class FakeAutonomyNode(_Participant):
    """sabeq_ros/sabeq_ros/autonomy_node.py, line by line (Node 2)."""

    def __init__(self, graph, routes_yaml=ROUTES_YAML):
        super().__init__(graph, "autonomy_node")
        import yaml
        config = yaml.safe_load(Path(routes_yaml).read_text())
        self.vehicle_speed, self.delay_per_person = 7.0, 0.5
        self.routes = config["routes"]
        for route_data in self.routes.values():
            route_data["distance"] = self.calculate_distance(route_data["waypoints"])
        self.active_route, self.scan_in_progress = None, False
        self.route_counts = {name: 0 for name in self.routes}
        self.graph.subscribe(self.name, T.PERSON_DETECTIONS, NODE2_QOS, self.detection_callback)
        self.sub(T.SCAN_ACTIVE_ROUTE, "string", NODE2_QOS, self.active_route_callback)
        self.sub(T.SCAN_COMPLETE, "bool", NODE2_QOS, self.scan_complete_callback)

    @staticmethod
    def calculate_distance(waypoints):
        return sum(math.hypot(x2 - x1, y2 - y1)
                   for (x1, y1), (x2, y2) in zip(waypoints, waypoints[1:]))

    def active_route_callback(self, route_name):
        if route_name not in self.routes:
            return
        if not self.scan_in_progress:
            self.route_counts = {name: 0 for name in self.routes}
            self.scan_in_progress = True
        self.active_route = route_name

    def detection_callback(self, message):
        if not self.scan_in_progress or self.active_route is None:
            return
        current_count = len(message.detections)
        if current_count > self.route_counts[self.active_route]:
            self.route_counts[self.active_route] = current_count

    def scan_complete_callback(self, data):
        if not data or not self.scan_in_progress:
            return
        best_route, best_time, scores = None, float("inf"), {}
        for route_name, route_data in self.routes.items():
            distance = route_data["distance"]
            people = self.route_counts[route_name]
            estimated_time = distance / self.vehicle_speed + people * self.delay_per_person
            scores[route_name] = {"distanceM": round(distance, 2), "peopleCount": people,
                                  "estimatedTimeS": round(estimated_time, 2)}
            if estimated_time < best_time:
                best_time, best_route = estimated_time, route_name
        self.graph.publish(self.name, T.ROUTE_SCORES, NODE2_QOS, MSG_TYPES.String(json.dumps(scores)))
        self.graph.publish(self.name, T.SELECTED_ROUTE, NODE2_QOS, MSG_TYPES.String(best_route))
        self.active_route, self.scan_in_progress = None, False


class FakeYolo(_Participant):
    """yolo_node: a Detection2DArray per processed frame (stamp = capture time)."""

    def __init__(self, graph, world, flight, period=0.3):
        super().__init__(graph, "yolo_node")
        self.world, self.flight, self.period = world, flight, period
        self._next = 0.0
        self._next_image = 0.0

    def people_in_view(self):
        yaw = self.flight.state().yaw_deg
        fs = self.flight.state()
        if not fs.in_air:
            return 0
        for r in self.world.routes.values():
            if abs((yaw - r.scan_yaw_ned_deg + 180) % 360 - 180) < 15.0:
                return r.scan_people
        return 0

    def step(self, t):
        if t >= self._next:
            self._next = t + self.period
            n = self.people_in_view()
            dets = [SimpleNamespace(
                bbox=SimpleNamespace(center=SimpleNamespace(position=SimpleNamespace(
                    x=40.0 + 30 * i, y=600.0), theta=0.0), size_x=12.0, size_y=46.0),
                results=[SimpleNamespace(hypothesis=SimpleNamespace(class_id="person", score=0.8))],
                id="") for i in range(n)]
            msg = Detection2DArray(header=Header(stamp=Time.of(t)), detections=dets)
            self.graph.publish(self.name, T.PERSON_DETECTIONS, YOLO_DETECTIONS_QOS, msg)
        if t >= self._next_image:
            self._next_image = t + 1.0
            w, h = 64, 48
            self.graph.publish("ros_gz_bridge", T.CAMERA_IMAGE, CAMERA_QOS, Image(
                header=Header(stamp=Time.of(t)), height=h, width=w, encoding="rgb8", step=w * 3,
                data=bytes([90, 120, 80]) * (w * h)))


class RosWorldSim:
    """mission_node + world_node (real logic) + Node 1 + Node 2 on the fake graph."""

    def __init__(self, graph, *, mission_params=None, flight=None, world_kw=None):
        sm = self.sm = sabeq_mission()
        self.graph = graph
        self.t = 0.0
        graph.clock = lambda: self.t
        world = sm.world.load_world(str(WORLD_META), str(ROUTES_YAML))
        self.world = world
        self.flight = flight or sm.flight.FakeFlight()
        self.mission_node = _Participant(graph, "mission_node")
        self.world_node = _Participant(graph, "world_node")
        self.mission = sm.mission_logic.MissionLogic(
            world, mission_params or sm.mission_logic.MissionParams(), self.flight,
            self._mission_pub, lambda: self.t)
        self.pose_sink = sm.pose_sink.FakePoseSink()
        self.audio = sm.audio.FakeAudio()
        self.world_logic = sm.world_logic.WorldLogic(world, self._world_pub, lambda: self.t,
                                                     self.pose_sink, self.audio,
                                                     **(world_kw or {}))
        m, w = self.mission, self.world_logic
        mn, wn = self.mission_node, self.world_node
        mn.sub(T.MISSION_COMMAND, "json", RELIABLE_20, self._mission_cmd)
        mn.sub(T.PAYLOAD_CMD, "json", RELIABLE_20, m.on_payload_cmd)
        mn.sub(T.SELECTED_ROUTE, "string", RELIABLE_10, m.on_selected_route)
        mn.sub(T.CART_STATUS, "json", RELIABLE_10, m.on_cart_status)
        mn.sub(T.CART_POSE, "json", BEST_EFFORT_5, m.on_cart_pose)
        graph.subscribe("mission_node", T.PERSON_DETECTIONS, RELIABLE_10,
                        lambda msg: m.on_detection(msg.header.stamp.sec
                                                   + msg.header.stamp.nanosec * 1e-9))
        wn.sub(T.SCAN_ACTIVE_ROUTE, "string", RELIABLE_10, w.on_active_route)
        wn.sub(T.MISSION_COMMAND, "json", RELIABLE_20, self._world_cmd)
        wn.sub(T.DRONE_POSITION, "json", BEST_EFFORT_5, w.on_drone_position)
        wn.sub(T.PAYLOAD_CMD, "json", RELIABLE_20, w.on_payload_cmd)
        self.node2 = FakeAutonomyNode(graph)
        from app.config import Settings
        from app.world import load_world as backend_world   # scan-zone people per route
        self.yolo = FakeYolo(graph, backend_world(Settings(_env_file=None)), self.flight)
        self.error = None
        self.world_commands = []
        self.running = True

    def _mission_cmd(self, cmd):
        if not self.mission_node.deaf:
            self.mission.on_command(cmd)

    def _world_cmd(self, cmd):
        self.world_commands.append(cmd.get("command"))
        if not self.world_node.deaf:
            self.world_logic.on_command(cmd)

    def _mission_pub(self, topic, payload):
        kind, qos = MISSION_NODE_PUB[topic]
        self.mission_node.pub(topic, kind, qos, payload)

    def _world_pub(self, topic, payload):
        kind, qos = WORLD_NODE_PUB[topic]
        self.world_node.pub(topic, kind, qos, payload)

    def step(self, dt=0.05):
        self.t = round(self.t + dt, 6)
        self.flight.step(dt)
        self.yolo.step(self.t)
        self.mission.tick()
        self.world_logic.tick()

    async def run(self, real_per_step=0.002):
        """Step the sim inside the app's loop: 0.05 s of sim time per `real_per_step`."""
        while self.running:
            try:
                self.step()
            except Exception as exc:  # noqa: BLE001  surfaced by the tests
                self.error = exc
                raise
            await asyncio.sleep(real_per_step)


__all__ = ["AVAILABLE", "RosWorldSim", "BEST_EFFORT_1", "LATCHED_1"]
