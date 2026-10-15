#!/usr/bin/env python3
"""SABEQ mission node v2.2.

Keeps the working takeoff + three-route scan flow, but adds two things needed by the
real dashboard integration:

1) Publishes REAL PX4 / MAVSDK telemetry on /drone/position and /drone/status.
2) During escort, keeps the drone approximately `lead_m` metres ahead of the real
   golf-cart odometry instead of flying an unrelated precomputed marker.

The route recommendation still comes from autonomy_node.  The backend adapter gates the
actual execution route onto /execution/selected_route and remaps it to /selected_route
when this node is launched.
"""

import asyncio
import json
import math
import os
import threading
from pathlib import Path

import rclpy
import yaml
from ament_index_python.packages import get_package_share_directory
from mavsdk import System
from mavsdk.offboard import OffboardError, PositionNedYaw, VelocityNedYaw
from nav_msgs.msg import Odometry
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node
from std_msgs.msg import Bool, String


DRONE_ID = "SBQ-01"


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
    """Distance along a polyline of the point closest to p."""
    if len(path) < 2:
        return 0.0
    best_d = float("inf")
    best_s = 0.0
    walked = 0.0
    for a, b in zip(path, path[1:]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        seg2 = dx * dx + dy * dy
        seg = math.sqrt(seg2)
        if seg < 1e-9:
            continue
        t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / seg2))
        q = (a[0] + t * dx, a[1] + t * dy)
        d = math.dist(p, q)
        if d < best_d:
            best_d = d
            best_s = walked + t * seg
        walked += seg
    return best_s


def _along(path, s):
    """Return east, north, ENU yaw at distance s along path."""
    if not path:
        return 0.0, 0.0, 0.0
    if len(path) == 1:
        return path[0][0], path[0][1], 0.0
    remaining = max(0.0, float(s))
    last_yaw = 0.0
    for a, b in zip(path, path[1:]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        seg = math.hypot(dx, dy)
        if seg < 1e-9:
            continue
        last_yaw = math.atan2(dy, dx)
        if remaining <= seg:
            t = remaining / seg
            return a[0] + t * dx, a[1] + t * dy, last_yaw
        remaining -= seg
    return path[-1][0], path[-1][1], last_yaw


def _yaw_from_quaternion(q):
    siny_cosp = 2.0 * (q.w * q.z + q.x * q.y)
    cosy_cosp = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
    return math.atan2(siny_cosp, cosy_cosp)


class MissionNode(Node):
    def __init__(self, asyncio_loop):
        super().__init__("mission_node")
        self.asyncio_loop = asyncio_loop

        # -------------------------------------------------
        # Shared route configuration
        # -------------------------------------------------
        package_share = get_package_share_directory("sabeq_ros")
        routes_file = os.path.join(package_share, "config", "routes.yaml")
        with open(routes_file, "r", encoding="utf-8") as file:
            self.config = yaml.safe_load(file)

        self.routes = self.config["routes"]
        self.scan_order = self.config["scan_order"]
        self.home_east, self.home_north = self.config["home_enu"]
        self.hover_east, self.hover_north = self.config["hover_point_enu"]
        self.start_east, self.start_north = self.config.get("start", [0.0, 0.0])
        self.incident_east, self.incident_north = self.config.get("incident", [0.0, 112.0])

        # Visual rescue / return leg uses the First Aid stop from the same world metadata.
        # Nothing here changes the outbound route selection or PX4 telemetry source.
        self.return_extra_points = []
        meta_path = Path.home() / "emergency_drone" / "world_meta.json"
        try:
            meta = json.loads(meta_path.read_text())
            self.return_extra_points = [
                (float(q[0]), float(q[1]))
                for q in (meta.get("first_aid", {}).get("cart_route_enu") or [])
            ]
        except Exception as exc:
            self.get_logger().warning(f"Return stop metadata unavailable: {exc}")

        self.flight_altitude = float(self.config.get("flight_altitude_m", 8.0))
        self.scan_settle_s = float(self.config.get("scan_settle_s", 1.5))
        self.scan_hold_s = float(self.config.get("scan_hold_s", 4.0))

        # -------------------------------------------------
        # Runtime parameters
        # -------------------------------------------------
        self.declare_parameter("connection_url", "udpin://0.0.0.0:14540")
        self.declare_parameter("mission_id", "")
        self.declare_parameter("lead_m", 30.0)
        self.declare_parameter("escort_update_hz", 10.0)

        self.connection_url = self.get_parameter("connection_url").value
        mission_id = str(self.get_parameter("mission_id").value).strip()
        self.mission_id = mission_id or None
        self.lead_m = float(self.get_parameter("lead_m").value)
        self.escort_update_hz = max(1.0, float(self.get_parameter("escort_update_hz").value))

        self.declare_parameter("escort_cruise_mps", 12.0)
        self.declare_parameter("escort_vel_max_mps", 15.0)
        self.escort_cruise_mps = max(1.0, float(self.get_parameter("escort_cruise_mps").value))
        self.escort_vel_max_mps = max(
            self.escort_cruise_mps,
            float(self.get_parameter("escort_vel_max_mps").value),
        )

        # -------------------------------------------------
        # ROS publishers
        # -------------------------------------------------
        self.active_route_publisher = self.create_publisher(String, "/scan/active_route", 10)
        self.scan_complete_publisher = self.create_publisher(Bool, "/scan/complete", 10)
        self.drone_position_publisher = self.create_publisher(String, "/drone/position", 5)
        self.drone_status_publisher = self.create_publisher(String, "/drone/status", 10)

        # -------------------------------------------------
        # ROS subscribers
        # -------------------------------------------------
        self.create_subscription(String, "/selected_route", self.selected_route_callback, 10)
        self.create_subscription(Odometry, "/golf_cart/odom", self.cart_odom_callback, 10)
        self.create_subscription(Bool, "/golf_cart/arrived", self.cart_arrived_callback, 10)
        self.create_subscription(Bool, "/golf_cart/returned", self.cart_returned_callback, 10)
        self.create_subscription(Bool, "/rescue/return_start", self.return_start_callback, 10)
        self.create_subscription(String, "/drone/payload_cmd", self.payload_callback, 10)

        # -------------------------------------------------
        # Decision + cart state
        # -------------------------------------------------
        self.selected_route = None
        self.selected_route_event = asyncio.Event()
        self.return_start_event = asyncio.Event()
        self.cart_east = float(self.start_east)
        self.cart_north = float(self.start_north)
        self.cart_yaw = 0.0
        self.cart_speed_mps = 0.0
        self.cart_arrived = False
        self.cart_returned = False

        # -------------------------------------------------
        # Live PX4 telemetry mirrored to ROS
        # -------------------------------------------------
        self.phase = "taking_off"
        self.active_scan_route = None
        self.scan_complete = False
        self.px4_north_m = 0.0
        self.px4_east_m = 0.0
        self.px4_down_m = 0.0
        self.px4_speed_mps = 0.0
        self.px4_heading_deg = 0.0
        self.battery_pct = None
        self.speaker = False
        self.led = False
        self.fault = None

        self.get_logger().info("SABEQ Mission Node v2 started")
        self.get_logger().info(
            f"REAL PX4 telemetry enabled | escort lead={self.lead_m:.1f} m | cruise={self.escort_cruise_mps:.1f} m/s"
        )

    # =====================================================
    # ROS communication
    # =====================================================
    def publish_active_route(self, route_name):
        self.active_scan_route = route_name or None
        self.active_route_publisher.publish(String(data=route_name))
        if route_name:
            self.get_logger().info(f"Scanning route: {route_name}")

    def publish_scan_complete(self):
        self.scan_complete = True
        self.scan_complete_publisher.publish(Bool(data=True))
        self.get_logger().info("Full scan completed")

    def selected_route_callback(self, message):
        route_name = message.data.strip()
        if route_name not in self.routes:
            self.get_logger().warning(f"Invalid selected route received: {route_name}")
            return
        self.selected_route = route_name
        self.get_logger().info(f"Selected route received: {route_name}")
        self.asyncio_loop.call_soon_threadsafe(self.selected_route_event.set)

    def cart_odom_callback(self, message):
        p = message.pose.pose.position
        self.cart_east = float(p.x)
        self.cart_north = float(p.y)
        self.cart_yaw = _yaw_from_quaternion(message.pose.pose.orientation)
        v = message.twist.twist.linear
        self.cart_speed_mps = math.hypot(float(v.x), float(v.y))

    def cart_arrived_callback(self, message):
        self.cart_arrived = bool(message.data)

    def cart_returned_callback(self, message):
        self.cart_returned = bool(message.data)

    def return_start_callback(self, message):
        if not message.data:
            return
        self.get_logger().info("Rescue sequence complete -> return escort requested")
        self.asyncio_loop.call_soon_threadsafe(self.return_start_event.set)

    def payload_callback(self, message):
        try:
            data = json.loads(message.data)
        except (TypeError, ValueError):
            return
        mid = data.get("missionId")
        if self.mission_id is not None and mid not in (None, self.mission_id):
            return
        if data.get("speaker") is not None:
            self.speaker = bool(data["speaker"])
        if data.get("led") is not None:
            self.led = bool(data["led"])

    # =====================================================
    # Coordinates
    # =====================================================
    def world_enu_to_local_ned(self, east, north, altitude):
        local_east = east - self.home_east
        local_north = north - self.home_north
        return local_north, local_east, -altitude

    def local_ned_to_world_enu(self, north, east):
        return self.home_east + east, self.home_north + north

    @staticmethod
    def heading_ned(current_east, current_north, target_east, target_north):
        delta_east = target_east - current_east
        delta_north = target_north - current_north
        return math.degrees(math.atan2(delta_east, delta_north)) % 360.0

    def route_path(self, route_name):
        raw = self.routes[route_name].get("waypoints", [])
        return _dedup(
            [(self.start_east, self.start_north)]
            + raw
            + [(self.incident_east, self.incident_north)]
        )

    def escort_setpoint(self, path, path_len):
        """Position + velocity feed-forward ~lead_m ahead of the real cart on `path`.

        A bare position setpoint makes PX4 trail it by roughly speed / MPC_XY_P (~9 m at
        30 km/h), which erodes the lead until the cart gate stops the cart, the target
        freezes and the drone brakes: a stop/go cycle. Feeding the cart's speed along the
        route tangent lets PX4 track the moving target without that lag.
        """
        cart_s = _project_s(path, (self.cart_east, self.cart_north))
        target_s = min(path_len, cart_s + self.lead_m)
        target_e, target_n, enu_yaw = _along(path, target_s)
        yaw_ned = (90.0 - math.degrees(enu_yaw)) % 360.0
        north, east, down = self.world_enu_to_local_ned(target_e, target_n, self.flight_altitude)
        speed = self.cart_speed_mps if target_s < path_len else 0.0
        position = PositionNedYaw(north, east, down, yaw_ned)
        velocity = VelocityNedYaw(
            speed * math.sin(enu_yaw), speed * math.cos(enu_yaw), 0.0, yaw_ned
        )
        return cart_s, position, velocity

    def return_path(self, route_name):
        path = list(reversed(self.route_path(route_name)))
        return _dedup(path + self.return_extra_points)

    # =====================================================
    # Backend-facing REAL telemetry
    # =====================================================
    def _sim_time(self):
        return round(self.get_clock().now().nanoseconds * 1e-9, 3)

    def publish_drone_position(self):
        east, north = self.local_ned_to_world_enu(self.px4_north_m, self.px4_east_m)
        payload = {
            "droneId": DRONE_ID,
            "missionId": self.mission_id,
            "simTimeS": self._sim_time(),
            "source": "px4_mavsdk",
            "localM": {"e": round(east, 3), "n": round(north, 3)},
            "altitudeM": round(-self.px4_down_m, 2),
            "headingDeg": round(self.px4_heading_deg % 360.0, 2),
            "speedMps": round(self.px4_speed_mps, 2),
        }
        self.drone_position_publisher.publish(
            String(data=json.dumps(payload, ensure_ascii=False))
        )

    def _scan_payload(self):
        if self.phase == "taking_off":
            altitude = max(0.0, -self.px4_down_m)
            progress = min(1.0, altitude / max(0.1, self.flight_altitude))
            return {"phase": "climb", "progress": round(progress, 3), "coveragePct": 0.0}
        if self.phase != "scanning":
            return None
        if self.scan_complete:
            return {"phase": "done", "progress": 1.0, "coveragePct": 100.0}
        if self.active_scan_route in self.scan_order:
            idx = self.scan_order.index(self.active_scan_route)
            coverage = 100.0 * idx / max(1, len(self.scan_order))
            progress = 0.25 + 0.75 * idx / max(1, len(self.scan_order))
            return {
                "phase": "rotate",
                "progress": round(progress, 3),
                "coveragePct": round(coverage, 1),
            }
        return {"phase": "rotate", "progress": 0.25, "coveragePct": 0.0}

    def publish_drone_status(self):
        camera_mode = "scan_360" if self.phase in ("taking_off", "scanning") else "forward"
        payload = {
            "droneId": DRONE_ID,
            "source": "px4_mavsdk",
            "simTimeS": self._sim_time(),
            "missionId": self.mission_id,
            "state": self.phase,
            "armed": self.phase not in ("docked", "offline"),
            "dockedOnCart": False,
            "batteryPct": None if self.battery_pct is None else round(self.battery_pct, 1),
            "flightTimeLeftS": None,
            "link": "ok" if self.fault is None else "lost",
            "payload": {"camera": True, "speaker": True, "led": True},
            "payloadState": {"speaker": self.speaker, "led": self.led},
            "cameraMode": camera_mode,
            "scan": self._scan_payload(),
            "fault": self.fault,
        }
        self.drone_status_publisher.publish(
            String(data=json.dumps(payload, ensure_ascii=False))
        )


# =========================================================
# MAVSDK waiting helpers
# =========================================================
async def wait_for_connection(drone):
    async for state in drone.core.connection_state():
        if state.is_connected:
            return


async def wait_for_local_position(drone):
    async for health in drone.telemetry.health():
        if health.is_local_position_ok:
            return


# Wall-clock limits. They are generous because Gazebo can run well below real time on a
# loaded PC (takeoff to 8 m then takes minutes, not seconds).
async def _wait(coro, timeout, what):
    try:
        await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError:
        raise asyncio.TimeoutError(f"{what} not reached after {timeout:.0f} s") from None


async def wait_for_altitude(node, altitude, timeout=180.0):
    async def wait():
        while True:
            if -node.px4_down_m >= altitude - 0.5:
                return
            await asyncio.sleep(0.1)
    await _wait(wait(), timeout, f"takeoff altitude {altitude:.1f} m "
                f"(drone at {-node.px4_down_m:.1f} m)")


async def wait_for_yaw(node, target_yaw, timeout=60.0):
    async def wait():
        while True:
            current_yaw = node.px4_heading_deg % 360.0
            error = abs((current_yaw - target_yaw + 180.0) % 360.0 - 180.0)
            if error <= 5.0:
                return
            await asyncio.sleep(0.1)
    await _wait(wait(), timeout, f"yaw {target_yaw:.0f} deg "
                f"(drone at {node.px4_heading_deg % 360.0:.0f} deg)")


async def wait_for_position(
    node,
    target_north,
    target_east,
    target_down,
    horizontal_tolerance=1.5,
    vertical_tolerance=1.0,
    timeout=180.0,
):
    async def wait():
        while True:
            horizontal_distance = math.hypot(
                target_north - node.px4_north_m,
                target_east - node.px4_east_m,
            )
            vertical_distance = abs(target_down - node.px4_down_m)
            if horizontal_distance <= horizontal_tolerance and vertical_distance <= vertical_tolerance:
                return
            await asyncio.sleep(0.1)
    await _wait(wait(), timeout, "scan hover point")


# =========================================================
# Live telemetry tasks
# =========================================================
async def position_telemetry_loop(node, drone):
    async for data in drone.telemetry.position_velocity_ned():
        node.px4_north_m = float(data.position.north_m)
        node.px4_east_m = float(data.position.east_m)
        node.px4_down_m = float(data.position.down_m)
        node.px4_speed_mps = math.hypot(
            float(data.velocity.north_m_s),
            float(data.velocity.east_m_s),
        )
        node.publish_drone_position()


async def attitude_telemetry_loop(node, drone):
    async for attitude in drone.telemetry.attitude_euler():
        node.px4_heading_deg = float(attitude.yaw_deg) % 360.0


async def battery_telemetry_loop(node, drone):
    async for battery in drone.telemetry.battery():
        remaining = battery.remaining_percent
        if remaining is not None and math.isfinite(remaining):
            node.battery_pct = max(0.0, min(100.0, float(remaining) * 100.0))


async def status_telemetry_loop(node):
    while rclpy.ok():
        node.publish_drone_status()
        await asyncio.sleep(0.5)


async def configure_px4_escort_speed(node, drone):
    """Raise PX4 horizontal position-controller limits for the short Expo demo.

    The mission still uses position setpoints; these parameters only let PX4 move
    quickly enough to stay ahead of the 30 km/h golf cart. If a parameter is not
    available in the installed PX4 build, keep running with the existing value.
    """
    params = (
        ("MPC_XY_CRUISE", node.escort_cruise_mps),
        ("MPC_XY_VEL_MAX", node.escort_vel_max_mps),
    )
    for name, value in params:
        try:
            await drone.param.set_param_float(name, float(value))
            node.get_logger().info(f"PX4 {name}={value:.1f} m/s")
        except Exception as error:
            node.get_logger().warning(
                f"Could not set PX4 {name}; keeping vehicle default: {error}"
            )


# =========================================================
# Mission
# =========================================================
async def run_mission(node):
    drone = System()
    telemetry_tasks = []

    node.get_logger().info("Connecting to PX4...")
    await drone.connect(system_address=node.connection_url)
    await wait_for_connection(drone)
    node.get_logger().info("Connected to PX4")

    await wait_for_local_position(drone)
    node.get_logger().info("Local position ready")

    await configure_px4_escort_speed(node, drone)

    telemetry_tasks = [
        asyncio.create_task(position_telemetry_loop(node, drone)),
        asyncio.create_task(attitude_telemetry_loop(node, drone)),
        asyncio.create_task(battery_telemetry_loop(node, drone)),
        asyncio.create_task(status_telemetry_loop(node)),
    ]

    try:
        # -------------------------------------------------
        # Takeoff
        # -------------------------------------------------
        node.phase = "taking_off"
        await drone.action.set_takeoff_altitude(node.flight_altitude)
        node.get_logger().info("Arming...")
        await drone.action.arm()
        node.get_logger().info(f"Taking off to {node.flight_altitude:.1f} m...")
        await drone.action.takeoff()
        await wait_for_altitude(node, node.flight_altitude)
        node.get_logger().info("Scan altitude reached")

        # -------------------------------------------------
        # Prepare Offboard at scan point
        # -------------------------------------------------
        first_route = node.scan_order[0]
        first_yaw = float(node.routes[first_route]["scan_yaw_ned_deg"])
        hover_north, hover_east, hover_down = node.world_enu_to_local_ned(
            node.hover_east,
            node.hover_north,
            node.flight_altitude,
        )

        await drone.offboard.set_position_ned(
            PositionNedYaw(hover_north, hover_east, hover_down, first_yaw)
        )
        try:
            await drone.offboard.start()
        except OffboardError as error:
            node.get_logger().error(f"Offboard start failed: {error}")
            node.fault = {"code": "offboard_start", "message": str(error)}
            await drone.action.hold()
            return

        await drone.offboard.set_position_ned(
            PositionNedYaw(hover_north, hover_east, hover_down, first_yaw)
        )
        await wait_for_position(node, hover_north, hover_east, hover_down)

        # -------------------------------------------------
        # Scan routes
        # -------------------------------------------------
        node.phase = "scanning"
        for route_name in node.scan_order:
            route = node.routes[route_name]
            yaw = float(route["scan_yaw_ned_deg"])

            node.publish_active_route("")
            node.get_logger().info(f"Turning toward {route_name} ({yaw:.1f} deg NED)")
            await drone.offboard.set_position_ned(
                PositionNedYaw(hover_north, hover_east, hover_down, yaw)
            )
            await wait_for_yaw(node, yaw)
            await asyncio.sleep(node.scan_settle_s)

            node.publish_active_route(route_name)
            await asyncio.sleep(node.scan_hold_s)

        node.publish_active_route("")
        node.selected_route = None
        node.selected_route_event.clear()
        node.publish_scan_complete()
        node.get_logger().info("Waiting for route decision...")

        try:
            await asyncio.wait_for(node.selected_route_event.wait(), timeout=90.0)
        except asyncio.TimeoutError:
            node.get_logger().error("Timed out waiting for /selected_route")
            node.fault = {"code": "route_timeout", "message": "No execution route received"}
            await drone.action.hold()
            return

        selected_route = node.selected_route
        node.get_logger().info(f"Executing selected route: {selected_route}")

        # -------------------------------------------------
        # Dynamic high-speed escort: target = real cart position + lead_m
        # -------------------------------------------------
        path = node.route_path(selected_route)
        path_len = _path_length(path)
        node.phase = "escorting"
        node.cart_arrived = False
        dt = 1.0 / node.escort_update_hz

        node.get_logger().info(
            f"Dynamic escort started | route={selected_route} | lead={node.lead_m:.1f} m"
        )

        while rclpy.ok() and not node.cart_arrived:
            cart_s, position, velocity = node.escort_setpoint(path, path_len)
            await drone.offboard.set_position_velocity_ned(position, velocity)

            if cart_s >= path_len - 1.0:
                break
            await asyncio.sleep(dt)

        # Finish at the incident and hold there.
        final_yaw = node.heading_ned(
            node.cart_east,
            node.cart_north,
            node.incident_east,
            node.incident_north,
        )
        final_north, final_east, final_down = node.world_enu_to_local_ned(
            node.incident_east,
            node.incident_north,
            node.flight_altitude,
        )
        await drone.offboard.set_position_ned(
            PositionNedYaw(final_north, final_east, final_down, final_yaw)
        )

        # Keep the backend-compatible drone state while rescue happens at the incident.
        node.phase = "escorting"
        node.get_logger().info("Selected route completed")
        node.get_logger().info("Drone holding at incident while medics load the patient")

        # Keep Offboard active on the incident setpoint while the visual rescue sequence runs.
        node.return_start_event.clear()
        while rclpy.ok() and not node.return_start_event.is_set():
            await drone.offboard.set_position_ned(
                PositionNedYaw(final_north, final_east, final_down, final_yaw)
            )
            await asyncio.sleep(0.5)

        if not rclpy.ok():
            return

        # -------------------------------------------------
        # Return escort: same selected route in reverse, then First Aid Center.
        # REAL PX4 remains the source of dashboard position / heading / speed.
        # -------------------------------------------------
        ret_path = node.return_path(selected_route)
        ret_len = _path_length(ret_path)
        node.phase = "returning_to_dock"
        node.cart_arrived = False
        node.cart_returned = False
        node.get_logger().info(
            f"RETURN escort started | route={selected_route} | lead={node.lead_m:.1f} m"
        )

        while rclpy.ok() and not node.cart_returned:
            cart_s, position, velocity = node.escort_setpoint(ret_path, ret_len)
            await drone.offboard.set_position_velocity_ned(position, velocity)
            if cart_s >= ret_len - 1.0:
                break
            await asyncio.sleep(dt)

        end_e, end_n, end_yaw_enu = _along(ret_path, ret_len)
        end_yaw_ned = (90.0 - math.degrees(end_yaw_enu)) % 360.0
        end_north, end_east, end_down = node.world_enu_to_local_ned(
            end_e, end_n, node.flight_altitude
        )
        await drone.offboard.set_position_ned(
            PositionNedYaw(end_north, end_east, end_down, end_yaw_ned)
        )
        node.get_logger().info("Return route completed; drone holding at First Aid Center")

        try:
            await drone.offboard.stop()
        except OffboardError:
            pass
        await drone.action.hold()

        while rclpy.ok():
            await asyncio.sleep(1.0)

    finally:
        for task in telemetry_tasks:
            task.cancel()
        if telemetry_tasks:
            await asyncio.gather(*telemetry_tasks, return_exceptions=True)


# =========================================================
# ROS + asyncio
# =========================================================
async def async_main(args=None):
    rclpy.init(args=args)
    loop = asyncio.get_running_loop()
    node = MissionNode(loop)

    executor = SingleThreadedExecutor()
    executor.add_node(node)
    spin_thread = threading.Thread(target=executor.spin, daemon=True)
    spin_thread.start()

    try:
        await run_mission(node)
    except asyncio.TimeoutError as error:
        node.get_logger().error(f"Mission timed out while waiting for vehicle state: {error}")
        node.phase = "fault"
        node.fault = {"code": "mission_timeout", "message": "Vehicle state timeout"}
        node.publish_drone_status()
    except Exception as error:
        node.get_logger().error(f"Mission failed: {error}")
        node.phase = "fault"
        node.fault = {"code": "mission_failed", "message": str(error)}
        node.publish_drone_status()
    finally:
        executor.shutdown()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
        spin_thread.join(timeout=1.0)


def main(args=None):
    try:
        asyncio.run(async_main(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
