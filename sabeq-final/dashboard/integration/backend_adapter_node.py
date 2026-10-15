#!/usr/bin/env python3
"""SABEQ0 compatibility adapter.

Bridges the four already-working SABEQ ROS nodes to the teammate FastAPI backend contract
without changing Node 1 / Node 2 / cart control logic.

Current SABEQ topics -> backend contract:
  /golf_cart/odom        -> /cart/pose
  /golf_cart/arrived     -> /cart/status
  /scan/active_route     -> derived /drone/status + scan event
  /mission/command       -> launch mission_node / forward final route
  /drone/payload_cmd     -> reflected in /drone/status.payloadState

Important route gate:
  Node 2 recommendation remains on /selected_route for the backend.
  The route actually executed is published here on /execution/selected_route only after
  the backend confirms / auto-selects it.

The outbound backend contract remains unchanged.  A local visual rescue node can trigger
/rescue/return_start after the patient is loaded; the adapter then applies the same real-PX4
lead governor on the return leg without inventing drone telemetry.
"""

import json
import math
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Optional

import rclpy
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import Bool, String


WORLD_META_DEFAULT = str(Path.home() / "emergency_drone" / "world_meta.json")
DRONE_ID = "SBQ-01"
CART_ID = "CART-01"
FLIGHT_ALTITUDE_M = 8.0
LEAD_M = 30.0
MIN_CART_LEAD_M = 18.0
RESUME_CART_LEAD_M = 25.0


def _dedup(points):
    out = []
    for p in points:
        q = (float(p[0]), float(p[1]))
        if not out or math.dist(out[-1], q) > 1e-6:
            out.append(q)
    return out


def _path_length(path):
    return sum(math.dist(a, b) for a, b in zip(path, path[1:]))


def _project_s(path, p):
    """Distance along polyline of point closest to p."""
    best_d = float("inf")
    best_s = 0.0
    walked = 0.0
    for a, b in zip(path, path[1:]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        seg2 = dx * dx + dy * dy
        seg = math.sqrt(seg2)
        t = 0.0 if seg2 < 1e-12 else max(
            0.0,
            min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / seg2),
        )
        q = (a[0] + t * dx, a[1] + t * dy)
        d = math.dist(p, q)
        if d < best_d:
            best_d = d
            best_s = walked + t * seg
        walked += seg
    return best_s


def _along(path, s):
    """Return east, north, ENU yaw at distance s along path."""
    s = max(0.0, float(s))
    if len(path) < 2:
        return path[0][0], path[0][1], 0.0
    last_h = 0.0
    for a, b in zip(path, path[1:]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        seg = math.hypot(dx, dy)
        if seg < 1e-9:
            continue
        last_h = math.atan2(dy, dx)
        if s <= seg:
            t = s / seg
            return a[0] + t * dx, a[1] + t * dy, last_h
        s -= seg
    return path[-1][0], path[-1][1], last_h


def _yaw_from_quaternion(q):
    siny_cosp = 2.0 * (q.w * q.z + q.x * q.y)
    cosy_cosp = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
    return math.atan2(siny_cosp, cosy_cosp)


def _compass_deg(enu_yaw):
    return (90.0 - math.degrees(enu_yaw)) % 360.0


class BackendAdapterNode(Node):
    def __init__(self):
        super().__init__("backend_adapter")

        self.declare_parameter("world_meta_path", WORLD_META_DEFAULT)
        self.declare_parameter("spawn_mission_node", True)
        self.declare_parameter("mission_package", "sabeq_ros")
        self.declare_parameter("mission_executable", "mission_node")
        self.declare_parameter("flight_altitude_m", FLIGHT_ALTITUDE_M)
        self.declare_parameter("lead_m", LEAD_M)

        meta_path = Path(
            self.get_parameter("world_meta_path").get_parameter_value().string_value
        ).expanduser()
        self.meta = json.loads(meta_path.read_text())
        self.routes = self._load_routes(self.meta)
        self.scan_order = list(self.routes)
        self.start = tuple(map(float, self.meta.get("cart_start_enu", [0.0, 0.0])[:2]))
        self.incident = tuple(map(float, self.meta["incident_enu"]))
        self.hover = tuple(map(float, self.meta.get("hover_point_enu", self.start)))
        self.flight_altitude = float(
            self.get_parameter("flight_altitude_m").get_parameter_value().double_value
        )
        self.lead_m = float(self.get_parameter("lead_m").get_parameter_value().double_value)

        reliable10 = QoSProfile(depth=10, reliability=ReliabilityPolicy.RELIABLE)
        reliable20 = QoSProfile(depth=20, reliability=ReliabilityPolicy.RELIABLE)
        latched1 = QoSProfile(
            depth=1,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
        )

        # Backend-facing publishers.
        self.events_pub = self.create_publisher(String, "/mission_events", reliable20)
        self.drone_status_pub = self.create_publisher(String, "/drone/status", reliable10)
        self.drone_position_pub = self.create_publisher(String, "/drone/position", 5)
        self.cart_pose_pub = self.create_publisher(String, "/cart/pose", 5)
        self.cart_status_pub = self.create_publisher(String, "/cart/status", reliable10)

        # Existing nodes subscribe to this instead of Node 2's recommendation directly.
        self.execution_route_pub = self.create_publisher(
            String, "/execution/selected_route", reliable10
        )

        # Emergency stop publisher is only used if this adapter is shutting down.
        self.cart_cmd_pub = self.create_publisher(Twist, "/golf_cart/cmd_vel", 10)

        # High-speed safety governor: cart_node writes RAW velocity here. The adapter
        # only forwards it to Gazebo while the REAL PX4 drone is safely ahead.
        self.create_subscription(Twist, "/golf_cart/cmd_vel_raw", self._on_cart_cmd_raw, 10)

        # Backend -> adapter.
        self.create_subscription(String, "/mission/command", self._on_command, reliable20)
        self.create_subscription(String, "/drone/payload_cmd", self._on_payload, reliable20)
        self.create_subscription(String, "/incident", self._on_incident, latched1)

        # Existing SABEQ nodes -> adapter.
        self.create_subscription(String, "/scan/active_route", self._on_active_route, reliable10)
        self.create_subscription(Bool, "/scan/complete", self._on_scan_complete, reliable10)
        self.create_subscription(String, "/selected_route", self._on_recommendation, reliable10)
        self.create_subscription(Odometry, "/golf_cart/odom", self._on_cart_odom, 10)
        self.create_subscription(Bool, "/golf_cart/arrived", self._on_cart_arrived, 10)
        self.create_subscription(Bool, "/golf_cart/returned", self._on_cart_returned, 10)
        self.create_subscription(Bool, "/rescue/return_start", self._on_return_start, 10)
        self.create_subscription(String, "/rescue/status", self._on_rescue_status, 10)
        # Observe the real mission_node heartbeat on the same backend-facing topic.
        # Our own bootstrap messages are tagged source=adapter_bootstrap and ignored here.
        self.create_subscription(String, "/drone/status", self._observe_drone_status, reliable10)
        self.create_subscription(String, "/drone/position", self._observe_drone_position, 5)

        self.mission_id: Optional[str] = None
        self.incident_id: Optional[str] = None
        self.phase = "docked"  # docked | taking_off | scanning | escorting | arrived | returning | returned
        self.active_route: Optional[str] = None
        self.recommended_route: Optional[str] = None
        self.executed_route: Optional[str] = None
        self.scan_complete = False
        self.scan_event_sent = False
        self.launch_wall = None
        self.px4_status_seen = False
        self.px4_position_seen = False
        self.drone_e, self.drone_n = self.start
        self.cart_gate_paused = True
        self.last_reported_lead_m = 0.0
        self.mission_process: Optional[subprocess.Popen] = None

        self.cart_e, self.cart_n = self.start
        self.cart_yaw = float(self.meta.get("cart_start_enu", [0.0, 0.0, 0.0])[2])
        self.cart_speed_mps = 0.0
        self.cart_arrived = False
        self.cart_returned = False
        self.rescue_progress = "none"
        self.speaker = False
        self.led = False

        self.create_timer(0.2, self._publish_fast_telemetry)   # 5 Hz
        self.create_timer(1.0, self._publish_status)           # 1 Hz heartbeat
        self.create_timer(1.0, self._watch_mission_process)

        self.get_logger().info("SABEQ Backend Adapter started")
        self.get_logger().info(f"Routes: {self.scan_order}")
        self.get_logger().info(
            "Node 2 recommendation=/selected_route | execution=/execution/selected_route"
        )

    # ------------------------------------------------------------------
    # Configuration / helpers
    # ------------------------------------------------------------------
    def _load_routes(self, meta):
        start = tuple(map(float, meta.get("cart_start_enu", [0.0, 0.0])[:2]))
        incident = tuple(map(float, meta["incident_enu"]))
        out = {}
        for rid, data in meta["routes"].items():
            path = _dedup([start] + data.get("waypoints_enu", []) + [incident])
            out[rid] = {
                "path": path,
                "length": _path_length(path),
                "scan_yaw": float(data.get("scan_yaw_ned_deg", 0.0)),
            }
        return out

    def _return_path(self, route):
        if route not in self.routes:
            return None
        path = list(reversed(self.routes[route]["path"]))
        for p in self.meta.get("first_aid", {}).get("cart_route_enu", []) or []:
            q = (float(p[0]), float(p[1]))
            if not path or math.dist(path[-1], q) > 1e-6:
                path.append(q)
        return _dedup(path)

    def _sim_time(self):
        return round(self.get_clock().now().nanoseconds * 1e-9, 3)

    def _publish_json(self, publisher, payload):
        publisher.publish(String(data=json.dumps(payload, ensure_ascii=False)))

    def _mission_matches(self, message):
        mid = message.get("missionId")
        return self.mission_id is None or mid is None or mid == self.mission_id

    # ------------------------------------------------------------------
    # Backend commands
    # ------------------------------------------------------------------
    def _on_incident(self, msg):
        try:
            data = json.loads(msg.data)
        except (TypeError, ValueError):
            self.get_logger().warning("Ignoring invalid /incident JSON")
            return
        if data.get("active"):
            self.mission_id = data.get("missionId")
            self.incident_id = data.get("incidentId")
            self.get_logger().info(f"Active mission: {self.mission_id}")
        else:
            self._reset_runtime()

    def _on_payload(self, msg):
        try:
            data = json.loads(msg.data)
        except (TypeError, ValueError):
            return
        if not self._mission_matches(data):
            return
        if data.get("speaker") is not None:
            self.speaker = bool(data["speaker"])
        if data.get("led") is not None:
            self.led = bool(data["led"])
        self._publish_status()

    def _on_command(self, msg):
        try:
            data = json.loads(msg.data)
        except (TypeError, ValueError):
            self.get_logger().warning("Ignoring invalid /mission/command JSON")
            return

        command = data.get("command")
        command_id = data.get("commandId")
        mission_id = data.get("missionId")

        if command != "reset" and self.mission_id is not None and mission_id != self.mission_id:
            self._ack("mission_node", command_id, False, "unknown_mission")
            self._ack("world_node", command_id, False, "unknown_mission")
            return

        if command == "launch":
            self._handle_launch(data)
        elif command == "select_route":
            self._handle_select_route(data)
        elif command in ("start_return", "abort"):
            # Current four-node implementation does not contain the teammate's
            # rescue/return world_node. Reject honestly; backend keeps the current state.
            self._ack("mission_node", command_id, False, "unsupported_current_ros")
            self._ack("world_node", command_id, False, "unsupported_current_ros")
        elif command == "reset":
            self._ack("world_node", command_id, False, "unsupported_current_ros")

    def _handle_launch(self, data):
        cid = data.get("commandId")
        if self.phase not in ("docked",):
            self._ack("mission_node", cid, False, "invalid_state")
            self._ack("world_node", cid, False, "invalid_state")
            return

        if not self._spawn_mission_node():
            self._ack("mission_node", cid, False, "mission_node_start_failed")
            self._ack("world_node", cid, False, "mission_node_start_failed")
            return

        self.phase = "taking_off"
        self.launch_wall = time.monotonic()
        self.cart_arrived = False
        self.cart_returned = False
        self.px4_status_seen = False
        self.px4_position_seen = False
        self.drone_e, self.drone_n = self.start
        self.cart_gate_paused = True
        self.last_reported_lead_m = 0.0
        self.active_route = None
        self.recommended_route = None
        self.executed_route = None
        self.scan_complete = False
        self.scan_event_sent = False

        # Compatibility shim: adapter represents the current legacy mission/cart pair
        # to the richer backend command-ack contract.
        self._ack("mission_node", cid, True, None)
        self._ack("world_node", cid, True, None)
        self._event("mission_node", "takeoff_started", {})
        self.get_logger().info("Backend launch accepted; mission_node started")

    def _handle_select_route(self, data):
        cid = data.get("commandId")
        route = data.get("route")
        if route not in self.routes:
            self._ack("mission_node", cid, False, "unknown_route")
            self._ack("world_node", cid, False, "unknown_route")
            return
        if self.phase not in ("scanning", "taking_off"):
            self._ack("mission_node", cid, False, "invalid_state")
            self._ack("world_node", cid, False, "invalid_state")
            return

        self.executed_route = route
        self.phase = "escorting"
        self.cart_gate_paused = True
        self.execution_route_pub.publish(String(data=route))
        self._ack("mission_node", cid, True, None)
        self._ack("world_node", cid, True, None)
        self._event("mission_node", "escort_started", {"leg": "outbound", "route": route})
        self.get_logger().info(f"EXECUTION route released: {route}")

    def _ack(self, source, command_id, accepted, reason):
        if not command_id:
            return
        self._event(
            source,
            "command_ack",
            {"commandId": command_id, "accepted": bool(accepted), "reason": reason},
        )

    def _event(self, source, event, details):
        self._publish_json(
            self.events_pub,
            {
                "missionId": self.mission_id,
                "simTimeS": self._sim_time(),
                "source": source,
                "event": event,
                "details": details,
            },
        )

    # ------------------------------------------------------------------
    # Existing SABEQ topics
    # ------------------------------------------------------------------
    def _on_active_route(self, msg):
        route = msg.data.strip()
        self.active_route = route if route in self.routes else None
        if self.active_route and self.phase == "taking_off":
            self.phase = "scanning"
            if not self.scan_event_sent:
                self.scan_event_sent = True
                self._event(
                    "mission_node",
                    "scan_altitude_reached",
                    {"altitudeM": self.flight_altitude},
                )
                self.get_logger().info("Backend notified: scan altitude reached")
        self._publish_status()

    def _on_scan_complete(self, msg):
        if not msg.data:
            return
        self.scan_complete = True
        self.active_route = None
        self._event("mission_node", "scan_rotation_complete", {})
        self._publish_status()

    def _on_recommendation(self, msg):
        route = msg.data.strip()
        if route in self.routes:
            self.recommended_route = route
            self.get_logger().info(f"Node 2 recommendation observed: {route}")

    def _on_cart_odom(self, msg):
        p = msg.pose.pose.position
        self.cart_e, self.cart_n = float(p.x), float(p.y)
        self.cart_yaw = _yaw_from_quaternion(msg.pose.pose.orientation)
        v = msg.twist.twist.linear
        self.cart_speed_mps = math.hypot(float(v.x), float(v.y))

    def _on_cart_arrived(self, msg):
        if not msg.data:
            return
        self.cart_arrived = True
        self.phase = "arrived"
        self.cart_speed_mps = 0.0
        self._publish_cart_status()
        self._publish_status()
        self.get_logger().info("Golf cart reached incident; backend notified")

    def _on_return_start(self, msg):
        if not msg.data or not self.executed_route:
            return
        self.phase = "returning"
        self.cart_arrived = False
        self.cart_returned = False
        self.cart_gate_paused = True
        self.get_logger().info("Return leg started; real-PX4 lead governor switched to reverse route")

    def _on_rescue_status(self, msg):
        try:
            data = json.loads(msg.data)
        except (TypeError, ValueError):
            return
        self.rescue_progress = "patient_loaded" if data.get("patientLoaded") else "none"

    def _on_cart_returned(self, msg):
        if not msg.data:
            return
        self.phase = "returned"
        self.cart_returned = True
        self.cart_arrived = False
        self.cart_speed_mps = 0.0
        self.get_logger().info("Golf cart reached First Aid Center")

    def _observe_drone_status(self, msg):
        """Stop bootstrap heartbeat as soon as REAL PX4 mission telemetry is alive."""
        try:
            data = json.loads(msg.data)
        except (TypeError, ValueError):
            return
        if data.get("source") == "px4_mavsdk":
            if not self.px4_status_seen:
                self.get_logger().info("REAL PX4 /drone/status heartbeat detected")
            self.px4_status_seen = True

    def _observe_drone_position(self, msg):
        """Track REAL PX4 position so the cart can never overtake the drone."""
        try:
            data = json.loads(msg.data)
        except (TypeError, ValueError):
            return
        if data.get("source") != "px4_mavsdk":
            return
        local = data.get("localM") or {}
        try:
            self.drone_e = float(local["e"])
            self.drone_n = float(local["n"])
        except (KeyError, TypeError, ValueError):
            return
        self.px4_position_seen = True

    def _current_drone_lead_m(self):
        if not self.executed_route or not self.px4_position_seen:
            return None, None
        if self.phase == "returning":
            path = self._return_path(self.executed_route)
            path_len = _path_length(path)
        else:
            path = self.routes[self.executed_route]["path"]
            path_len = self.routes[self.executed_route]["length"]
        cart_s = _project_s(path, (self.cart_e, self.cart_n))
        drone_s = _project_s(path, (self.drone_e, self.drone_n))
        return drone_s - cart_s, max(0.0, path_len - cart_s)

    def _on_cart_cmd_raw(self, msg):
        """Scale cart velocity by how much lead the real PX4 drone has.

        Hard guarantee: below the minimum lead the cart is stopped. Between the minimum and
        the resume lead the cart's speed ramps proportionally instead of the old on/off gate,
        which slammed the cart to 0 and froze the drone's target (cart + lead), giving a
        visible stop / go cycle for both vehicles.
        """
        out = Twist()
        out.linear.x = msg.linear.x
        out.linear.y = msg.linear.y
        out.linear.z = msg.linear.z
        out.angular.x = msg.angular.x
        out.angular.y = msg.angular.y
        out.angular.z = msg.angular.z

        moving_forward = abs(float(msg.linear.x)) > 1e-3 or abs(float(msg.linear.y)) > 1e-3
        if self.phase in ("escorting", "returning") and moving_forward:
            lead, remaining = self._current_drone_lead_m()
            self.last_reported_lead_m = float(lead or 0.0)

            # Near the incident the drone cannot stay 25 m ahead because the path ends.
            # Shrink the required lead with remaining distance so the cart can finish.
            min_required = MIN_CART_LEAD_M
            resume_required = RESUME_CART_LEAD_M
            if remaining is not None:
                min_required = min(min_required, max(0.0, remaining - 1.0))
                resume_required = min(resume_required, max(0.0, remaining - 1.0))

            if lead is None:
                scale = 0.0
            elif lead >= resume_required:
                scale = 1.0
            elif lead <= min_required:
                scale = 0.0
            else:
                scale = (lead - min_required) / max(1e-6, resume_required - min_required)

            paused = scale <= 0.0
            if paused != self.cart_gate_paused and lead is not None:
                if paused:
                    self.get_logger().warning(f"Cart paused: drone lead dropped to {lead:.1f} m")
                else:
                    self.get_logger().info(f"Cart released: real PX4 drone lead={lead:.1f} m")
            self.cart_gate_paused = paused

            # Let cart_node keep steering (angular), but only advance as fast as the lead allows.
            out.linear.x *= scale
            out.linear.y *= scale
            out.linear.z *= scale

        self.cart_cmd_pub.publish(out)

    # ------------------------------------------------------------------
    # Mission process manager
    # ------------------------------------------------------------------
    def _spawn_mission_node(self):
        if self.mission_process is not None and self.mission_process.poll() is None:
            return True
        if not self.get_parameter("spawn_mission_node").get_parameter_value().bool_value:
            return True
        package = self.get_parameter("mission_package").get_parameter_value().string_value
        executable = self.get_parameter("mission_executable").get_parameter_value().string_value
        cmd = [
            "ros2", "run", package, executable,
            "--ros-args",
            "-r", "/selected_route:=/execution/selected_route",
            "-p", "use_sim_time:=true",
            "-p", f"mission_id:={self.mission_id or ''}",
            "-p", f"lead_m:={self.lead_m}",
            "-p", "escort_update_hz:=10.0",
            "-p", "escort_cruise_mps:=12.0",
            "-p", "escort_vel_max_mps:=15.0",
        ]
        try:
            self.mission_process = subprocess.Popen(cmd, env=os.environ.copy())
            return True
        except OSError as exc:
            self.get_logger().error(f"Could not start mission_node: {exc}")
            self.mission_process = None
            return False

    def _watch_mission_process(self):
        if self.mission_process is None:
            return
        code = self.mission_process.poll()
        if code is None:
            return
        if self.phase not in ("arrived", "docked") and code != 0:
            self.get_logger().error(f"mission_node exited with code {code}")
            self._event(
                "mission_node",
                "node_error",
                {"code": "mission_node_exit", "message": f"exit code {code}"},
            )
        self.mission_process = None

    # ------------------------------------------------------------------
    # Backend telemetry contract
    # ------------------------------------------------------------------
    def _publish_fast_telemetry(self):
        self._publish_cart_pose()
        # Before launch the drone is physically docked on the cart, so the adapter
        # provides the initial dashboard pose. After launch mission_node v2 becomes
        # the ONLY owner of /drone/position and publishes REAL MAVSDK / PX4 telemetry.
        if self.phase == "docked":
            self._publish_drone_position()

    def _publish_status(self):
        self._publish_cart_status()
        # Keep a short bootstrap heartbeat during PX4/MAVSDK connection so the backend's
        # 5 s watchdog does not falsely declare link_lost. The instant mission_node v2
        # publishes source=px4_mavsdk, this adapter stops publishing drone status.
        if self.phase == "docked" or (self.phase == "taking_off" and not self.px4_status_seen):
            self._publish_drone_status()

    def _publish_cart_pose(self):
        self._publish_json(
            self.cart_pose_pub,
            {
                "cartId": CART_ID,
                "simTimeS": self._sim_time(),
                "source": "gazebo",
                "localM": {"e": round(self.cart_e, 3), "n": round(self.cart_n, 3)},
                "headingDeg": round(_compass_deg(self.cart_yaw), 2),
                "speedKmh": round(self.cart_speed_mps * 3.6, 2),
            },
        )

    def _cart_state(self):
        if self.phase == "returned":
            return "at_hub"
        if self.phase == "returning":
            return "to_hub"
        if self.cart_arrived:
            return "at_incident"
        if self.executed_route:
            return "to_incident"
        return "at_hub"

    def _publish_cart_status(self):
        route = self.executed_route
        if route and self.phase in ("returning", "returned"):
            path = self._return_path(route)
            path_len = _path_length(path)
            leg = "return"
        else:
            path = self.routes[route]["path"] if route else None
            path_len = self.routes[route]["length"] if route else 0.0
            leg = "outbound" if route else None
        path_s = _project_s(path, (self.cart_e, self.cart_n)) if path else 0.0
        self._publish_json(
            self.cart_status_pub,
            {
                "cartId": CART_ID,
                "simTimeS": self._sim_time(),
                "missionId": self.mission_id,
                "state": self._cart_state(),
                "phase": (
                    "AT FIRST AID" if self.phase == "returned"
                    else ("RETURN TO FIRST AID" if self.phase == "returning"
                          else ("ARRIVED AT INCIDENT" if self.cart_arrived
                                else ("TO INCIDENT" if route else "AT HUB")))
                ),
                "route": route,
                "leg": leg,
                "pathS": round(path_s, 2),
                "pathLenM": round(path_len, 2),
                "speedMps": round(self.cart_speed_mps, 2),
                "retPhase": ("DONE" if self.phase == "returned" else ("RETURN" if self.phase == "returning" else None)),
                "rescue": {"progress": self.rescue_progress},
            },
        )

    def _derived_drone_pose(self):
        # Before launch: drone is docked on the cart roof.
        if self.phase == "docked":
            return self.cart_e, self.cart_n, 0.0, _compass_deg(self.cart_yaw), 0.0

        if self.phase == "taking_off":
            elapsed = max(0.0, time.monotonic() - (self.launch_wall or time.monotonic()))
            alt = min(self.flight_altitude, elapsed * 2.0)
            return self.hover[0], self.hover[1], alt, _compass_deg(self.cart_yaw), 0.0

        if self.phase == "scanning":
            heading = (
                self.routes[self.active_route]["scan_yaw"]
                if self.active_route in self.routes
                else _compass_deg(self.cart_yaw)
            )
            return self.hover[0], self.hover[1], self.flight_altitude, heading, 0.0

        if self.executed_route in self.routes:
            path = self.routes[self.executed_route]["path"]
            cart_s = _project_s(path, (self.cart_e, self.cart_n))
            target_s = min(self.routes[self.executed_route]["length"], cart_s + self.lead_m)
            e, n, yaw = _along(path, target_s)
            return e, n, self.flight_altitude, _compass_deg(yaw), max(self.cart_speed_mps, 0.0)

        return self.hover[0], self.hover[1], self.flight_altitude, 0.0, 0.0

    def _publish_drone_position(self):
        e, n, alt, heading, speed = self._derived_drone_pose()
        self._publish_json(
            self.drone_position_pub,
            {
                "droneId": DRONE_ID,
                "missionId": self.mission_id,
                "simTimeS": self._sim_time(),
                "localM": {"e": round(e, 3), "n": round(n, 3)},
                "altitudeM": round(alt, 2),
                "headingDeg": round(heading, 2),
                "speedMps": round(speed, 2),
            },
        )

    def _scan_status(self):
        if self.phase == "taking_off":
            return {"phase": "climb", "progress": 0.2, "coveragePct": 0.0}
        if self.phase != "scanning":
            return None
        if self.scan_complete:
            return {"phase": "done", "progress": 1.0, "coveragePct": 100.0}
        if self.active_route in self.scan_order:
            idx = self.scan_order.index(self.active_route)
            coverage = 100.0 * idx / max(1, len(self.scan_order))
            progress = 0.3 + 0.7 * idx / max(1, len(self.scan_order))
            return {"phase": "rotate", "progress": round(progress, 3),
                    "coveragePct": round(coverage, 1)}
        return {"phase": "rotate", "progress": 0.3, "coveragePct": 0.0}

    def _publish_drone_status(self):
        state = {
            "docked": "docked",
            "taking_off": "taking_off",
            "scanning": "scanning",
            "escorting": "escorting",
            "arrived": "escorting",
        }.get(self.phase, "offline")
        camera_mode = "off" if self.phase == "docked" else (
            "scan_360" if self.phase in ("taking_off", "scanning") else "forward"
        )
        self._publish_json(
            self.drone_status_pub,
            {
                "droneId": DRONE_ID,
                "source": "adapter_bootstrap",
                "simTimeS": self._sim_time(),
                "missionId": self.mission_id,
                "state": state,
                "armed": self.phase != "docked",
                "dockedOnCart": self.phase == "docked",
                # Current Node 3 does not expose battery telemetry on ROS; keep a stable
                # compatibility value rather than inventing a changing fake measurement.
                "batteryPct": 100.0,
                "flightTimeLeftS": 1560.0,
                "link": "ok",
                "payload": {"camera": True, "speaker": True, "led": True},
                "payloadState": {"speaker": self.speaker, "led": self.led},
                "cameraMode": camera_mode,
                "scan": self._scan_status(),
                "fault": None,
            },
        )

    # ------------------------------------------------------------------
    def _reset_runtime(self):
        self.mission_id = None
        self.incident_id = None
        self.phase = "docked"
        self.active_route = None
        self.recommended_route = None
        self.executed_route = None
        self.scan_complete = False
        self.scan_event_sent = False
        self.px4_status_seen = False
        self.px4_position_seen = False
        self.drone_e, self.drone_n = self.start
        self.cart_gate_paused = True
        self.last_reported_lead_m = 0.0
        self.cart_arrived = False
        self.cart_returned = False
        self.rescue_progress = "none"
        self.speaker = False
        self.led = False

    def destroy_node(self):
        try:
            stop = Twist()
            self.cart_cmd_pub.publish(stop)
            if self.mission_process is not None and self.mission_process.poll() is None:
                self.mission_process.send_signal(signal.SIGINT)
                try:
                    self.mission_process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.mission_process.terminate()
        finally:
            super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = BackendAdapterNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
