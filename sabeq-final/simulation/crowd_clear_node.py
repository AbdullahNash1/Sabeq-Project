#!/usr/bin/env python3
"""SABEQ Expo crowd-clearance visualizer (V2.4.1).

Important V2.4.1 fix:
- No Python Gazebo bindings are imported. On some Gazebo Harmonic installs the
  `gz.transport13` Python package is not installed even though the `gz` CLI is.
  That made the old node exit immediately at startup.
- This node uses the Gazebo CLI service `/world/<world>/set_pose_vector`, which
  is part of the normal Harmonic installation and is already used by the
  project's launch flow.
- ROS stays alive even if a Gazebo pose update fails, so dashboard/backend
  startup is never blocked by the crowd visualizer.

Inputs:
  /execution/selected_route  std_msgs/String
  /drone/position            std_msgs/String JSON (source=px4_mavsdk)

Output:
  /crowd_clear/status        std_msgs/String JSON
"""

import json
import math
import shutil
import subprocess
from pathlib import Path

import rclpy
from rclpy.node import Node
from std_msgs.msg import String


DEFAULT_META = Path.home() / "emergency_drone" / "world_meta.json"


def _dedup(points):
    out = []
    for p in points:
        q = (float(p[0]), float(p[1]))
        if not out or math.hypot(q[0] - out[-1][0], q[1] - out[-1][1]) > 1e-6:
            out.append(q)
    return out


def _project_s(path, point):
    """Distance along polyline to the point's closest projection."""
    px, py = point
    best_d = float("inf")
    best_s = 0.0
    acc = 0.0
    for (x0, y0), (x1, y1) in zip(path, path[1:]):
        vx, vy = x1 - x0, y1 - y0
        l2 = vx * vx + vy * vy
        seg = math.sqrt(l2)
        if seg < 1e-9:
            continue
        t = max(0.0, min(1.0, ((px - x0) * vx + (py - y0) * vy) / l2))
        cx, cy = x0 + t * vx, y0 + t * vy
        d = math.hypot(px - cx, py - cy)
        if d < best_d:
            best_d = d
            best_s = acc + t * seg
        acc += seg
    return best_s


def _quat_from_rpy(roll: float, pitch: float, yaw: float):
    cr, sr = math.cos(roll / 2.0), math.sin(roll / 2.0)
    cp, sp = math.cos(pitch / 2.0), math.sin(pitch / 2.0)
    cy, sy = math.cos(yaw / 2.0), math.sin(yaw / 2.0)
    return (
        cr * cp * cy + sr * sp * sy,
        sr * cp * cy - cr * sp * sy,
        cr * sp * cy + sr * cp * sy,
        cr * cp * sy - sr * sp * cy,
    )


class CrowdClearNode(Node):
    def __init__(self):
        super().__init__("sabeq_crowd_clear_node")

        self.declare_parameter("meta_path", str(DEFAULT_META))
        self.declare_parameter("trigger_ahead_m", 12.0)
        self.declare_parameter("move_speed_mps", 2.5)
        # CLI service calls are more portable but heavier than native bindings.
        # 2 Hz is visually smooth enough for the demo and keeps Gazebo responsive.
        self.declare_parameter("update_hz", 2.0)

        meta_path = Path(self.get_parameter("meta_path").value).expanduser()
        self.trigger_ahead = float(self.get_parameter("trigger_ahead_m").value)
        self.move_speed = float(self.get_parameter("move_speed_mps").value)
        self.update_hz = max(1.0, min(3.0, float(self.get_parameter("update_hz").value)))

        if not meta_path.is_file():
            raise RuntimeError(f"world metadata not found: {meta_path}")
        if shutil.which("gz") is None:
            raise RuntimeError("Gazebo CLI `gz` was not found in PATH")

        self.meta = json.loads(meta_path.read_text())
        self.world = self.meta.get("world", "expo_demo")
        self.route_meta = self.meta.get("routes") or {}
        self.routes = set(self.route_meta)
        self.start = tuple((self.meta.get("cart_start_enu") or [0.0, 0.0])[:2])
        self.incident = tuple((self.meta.get("incident_enu") or [0.0, 112.0])[:2])
        self.paths = {
            name: _dedup([self.start] + (data.get("waypoints_enu") or []) + [self.incident])
            for name, data in self.route_meta.items()
        }

        self.people = {}
        for raw in self.meta.get("people", []):
            route = raw.get("route")
            cx, cy = raw.get("clear_x"), raw.get("clear_y")
            if route not in self.routes or cx is None or cy is None or raw.get("s") is None:
                continue
            self.people[raw["name"]] = {
                "route": route,
                "s": float(raw["s"]),
                "x": float(raw["x"]),
                "y": float(raw["y"]),
                "home_x": float(raw["x"]),
                "home_y": float(raw["y"]),
                "clear_x": float(cx),
                "clear_y": float(cy),
                "yaw": float(raw.get("yaw", 0.0)),
                "group": bool(raw.get("group", False)),
                "size": int(raw.get("size", 1)),
                "state": "waiting",
                "travel": 0.0,
                "send_final": False,
            }

        self.active_route = None
        self.drone_xy = None
        self.drone_s = None
        self.last_clock_s = None
        self.triggered_models = 0
        self.cleared_models = 0
        self.cleared_people = 0
        self.gz_ok = None
        self.service_warned = False
        self.pose_service = f"/world/{self.world}/set_pose_vector"

        self.status_pub = self.create_publisher(String, "/crowd_clear/status", 10)
        self.create_subscription(String, "/execution/selected_route", self._on_route, 10)
        self.create_subscription(String, "/drone/position", self._on_drone_position, 10)
        self.create_timer(1.0 / self.update_hz, self._tick)
        self.create_timer(1.0, self._publish_status)

        self.get_logger().info(
            "Crowd clearance V2.4.1 ready | "
            f"routes={sorted(self.routes)} | people_models={len(self.people)} | "
            f"triggerAhead={self.trigger_ahead:.1f} m | speed={self.move_speed:.1f} m/s | "
            f"poseRate={self.update_hz:.1f} Hz"
        )
        self.get_logger().info("Gazebo control: CLI set_pose_vector (no gz Python bindings required)")
        self.get_logger().info("Movement is driven only by REAL /drone/position source=px4_mavsdk")
        self._publish_status()

    def _on_route(self, msg: String):
        route = msg.data.strip()
        if not route:
            return
        if route not in self.routes:
            self.get_logger().warning(f"Ignoring unknown execution route: {route}")
            return
        if route == self.active_route:
            return
        self.active_route = route
        self.drone_s = None
        self.triggered_models = 0
        self.cleared_models = 0
        self.cleared_people = 0
        for p in self.people.values():
            if p["route"] == route:
                p["x"], p["y"] = p["home_x"], p["home_y"]
                p["state"] = "waiting"
                p["travel"] = 0.0
                p["send_final"] = False
        models = [p for p in self.people.values() if p["route"] == route]
        physical_people = sum(p["size"] for p in models)
        self.get_logger().info(
            f"Execution route={route}; {len(models)} Gazebo crowd model(s) / "
            f"{physical_people} represented people armed for clearance"
        )
        self._publish_status()

    def _on_drone_position(self, msg: String):
        try:
            data = json.loads(msg.data)
        except (json.JSONDecodeError, TypeError):
            return
        if data.get("source") != "px4_mavsdk":
            return
        local = data.get("localM") or {}
        try:
            self.drone_xy = (float(local["e"]), float(local["n"]))
        except (KeyError, TypeError, ValueError):
            return
        if self.active_route:
            self.drone_s = _project_s(self.paths[self.active_route], self.drone_xy)

    @staticmethod
    def _advance(p, step_m: float):
        dx = p["clear_x"] - p["x"]
        dy = p["clear_y"] - p["y"]
        d = math.hypot(dx, dy)
        if d <= step_m or d < 1e-6:
            p["travel"] += d
            p["x"], p["y"] = p["clear_x"], p["clear_y"]
            p["state"] = "clear"
            p["send_final"] = True
            return
        p["x"] += dx / d * step_m
        p["y"] += dy / d * step_m
        p["travel"] += step_m
        if not p["group"]:
            p["yaw"] = math.atan2(dy, dx)

    def _request_text(self, updates):
        chunks = []
        for name, p, moving in updates:
            z = 0.0
            roll = 0.0
            pitch = 0.0
            if moving:
                phase = p["travel"] * math.pi / 0.75
                z = (0.025 if p["group"] else 0.05) * abs(math.sin(phase))
                if not p["group"]:
                    roll = math.radians(3.0) * math.sin(phase)
                    pitch = math.radians(7.0)
            qw, qx, qy, qz = _quat_from_rpy(roll, pitch, p["yaw"])
            safe_name = name.replace('\\', '\\\\').replace('"', '\\"')
            chunks.append(
                "pose { "
                f'name: "{safe_name}" '
                f'position {{ x: {p["x"]:.6f} y: {p["y"]:.6f} z: {z:.6f} }} '
                f'orientation {{ w: {qw:.9f} x: {qx:.9f} y: {qy:.9f} z: {qz:.9f} }} '
                "}"
            )
        return " ".join(chunks)

    def _send(self, updates):
        if not updates:
            return True
        req = self._request_text(updates)
        cmd = [
            "gz", "service",
            "-s", self.pose_service,
            "--reqtype", "gz.msgs.Pose_V",
            "--reptype", "gz.msgs.Boolean",
            "--timeout", "1200",
            "--req", req,
        ]
        try:
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=2.0,
                check=False,
            )
            ok = proc.returncode == 0
        except (subprocess.TimeoutExpired, OSError) as exc:
            ok = False
            proc = None
            err = str(exc)
        else:
            err = (proc.stderr or proc.stdout or "").strip()

        self.gz_ok = ok
        if ok:
            if self.service_warned:
                self.get_logger().info("Gazebo crowd pose service recovered")
            self.service_warned = False
            return True

        if not self.service_warned:
            self.get_logger().error(
                "Crowd pose update failed, but SABEQ will keep running. "
                f"service={self.pose_service} detail={err[:220]}"
            )
            self.service_warned = True
        return False

    def _sim_dt(self):
        now = self.get_clock().now().nanoseconds * 1e-9
        if self.last_clock_s is None:
            self.last_clock_s = now
            return 0.0
        dt = max(0.0, min(now - self.last_clock_s, 0.75))
        self.last_clock_s = now
        return dt

    def _tick(self):
        dt = self._sim_dt()
        if self.active_route is None or self.drone_s is None or dt <= 0.0:
            return

        updates = []
        newly_triggered = []
        newly_cleared = []
        for name, p in self.people.items():
            if p["route"] != self.active_route:
                continue
            if p["state"] == "waiting" and self.drone_s + self.trigger_ahead >= p["s"]:
                p["state"] = "moving"
                newly_triggered.append(name)
            if p["state"] == "moving":
                self._advance(p, self.move_speed * dt)
                updates.append((name, p, True))
                if p["state"] == "clear":
                    newly_cleared.append(name)
            elif p["state"] == "clear" and p["send_final"]:
                p["send_final"] = False
                updates.append((name, p, False))

        sent = self._send(updates)
        if sent:
            if newly_triggered:
                self.triggered_models += len(newly_triggered)
                self.get_logger().info(
                    f"REAL drone s={self.drone_s:.1f} m -> {len(newly_triggered)} crowd model(s) moving aside"
                )
            if newly_cleared:
                self.cleared_models += len(newly_cleared)
                self.cleared_people += sum(self.people[n]["size"] for n in newly_cleared)
                self.get_logger().info(
                    f"Roadside clear: +{len(newly_cleared)} model(s), represented people clear={self.cleared_people}"
                )

    def _publish_status(self):
        payload = {
            "source": "px4_mavsdk" if self.drone_xy is not None else "waiting_px4",
            "route": self.active_route,
            "droneS": None if self.drone_s is None else round(self.drone_s, 2),
            "triggeredModels": self.triggered_models,
            "clearedModels": self.cleared_models,
            "clearedPeople": self.cleared_people,
            "gazeboPoseOk": self.gz_ok,
            "poseService": self.pose_service,
            "transport": "gz_cli",
        }
        self.status_pub.publish(String(data=json.dumps(payload, separators=(",", ":"))))


def main(args=None):
    rclpy.init(args=args)
    node = None
    try:
        node = CrowdClearNode()
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
