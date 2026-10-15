#!/usr/bin/env python3

import json
import math
from pathlib import Path

import rclpy
from rclpy.node import Node

from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
from std_msgs.msg import String, Bool


META_PATH = Path.home() / "emergency_drone" / "world_meta.json"

CONTROL_HZ = 20.0

MAX_SPEED = 8.33
MAX_TURN_RATE = 1.2

WAYPOINT_TOLERANCE = 1.2

# نعطي الدرون كم ثانية يتقدم قبل تحرك القولف كارت
START_DELAY_SEC = 1.0
def clamp(value, minimum, maximum):
    return max(minimum, min(maximum, value))


def normalize_angle(angle):
    while angle > math.pi:
        angle -= 2.0 * math.pi

    while angle < -math.pi:
        angle += 2.0 * math.pi

    return angle


def quaternion_to_yaw(q):
    siny_cosp = 2.0 * (
        q.w * q.z +
        q.x * q.y
    )

    cosy_cosp = 1.0 - 2.0 * (
        q.y * q.y +
        q.z * q.z
    )

    return math.atan2(siny_cosp, cosy_cosp)


class CartNode(Node):

    def __init__(self):
        super().__init__("cart_node")

        self.get_logger().info("SABEQ Golf Cart Node started")

        # ----------------------------------------------------
        # Load world metadata
        # ----------------------------------------------------

        if not META_PATH.exists():
            raise RuntimeError(
                f"world_meta.json not found: {META_PATH}"
            )

        with META_PATH.open("r") as f:
            self.meta = json.load(f)

        self.routes = self.meta["routes"]

        self.get_logger().info(
            f"Loaded routes: {list(self.routes.keys())}"
        )

        # ----------------------------------------------------
        # Publishers
        # ----------------------------------------------------

        self.cmd_pub = self.create_publisher(
            Twist,
            "/golf_cart/cmd_vel",
            10
        )

        self.arrived_pub = self.create_publisher(
            Bool,
            "/golf_cart/arrived",
            10
        )

        self.returned_pub = self.create_publisher(
            Bool,
            "/golf_cart/returned",
            10
        )

        # ----------------------------------------------------
        # Subscribers
        # ----------------------------------------------------

        self.route_sub = self.create_subscription(
            String,
            "/selected_route",
            self.route_callback,
            10
        )

        self.odom_sub = self.create_subscription(
            Odometry,
            "/golf_cart/odom",
            self.odom_callback,
            10
        )

        self.return_sub = self.create_subscription(
            Bool,
            "/rescue/return_start",
            self.return_callback,
            10
        )

        # ----------------------------------------------------
        # State
        # ----------------------------------------------------

        self.x = None
        self.y = None
        self.yaw = None

        self.active = False

        self.waypoints = []
        self.waypoint_index = 0

        self.selected_route = None
        self.outbound_waypoints = []
        self.returning = False

        self.start_time = None

        # ----------------------------------------------------
        # Controller
        # ----------------------------------------------------

        self.timer = self.create_timer(
            1.0 / CONTROL_HZ,
            self.control_loop
        )

        self.get_logger().info(
            "Waiting for /selected_route ..."
        )

    # ========================================================
    # Selected route
    # ========================================================

    def route_callback(self, msg):

        requested_route = msg.data.strip()

        route_name = self.resolve_route_name(
            requested_route
        )

        if route_name is None:
            self.get_logger().error(
                f"Unknown route: {requested_route}"
            )
            return

        # منع تشغيل نفس المسار مرتين
        if self.active:
            return

        self.selected_route = route_name

        route_data = self.routes[route_name]

        self.waypoints = [
            (float(p[0]), float(p[1]))
            for p in route_data["waypoints_enu"]
        ]

        # بعد نهاية المسار:
        # كمل إلى Incident
        incident = self.meta.get("incident_enu")

        if incident:
            incident_point = (
                float(incident[0]),
                float(incident[1])
            )

            if self.waypoints[-1] != incident_point:
                self.waypoints.append(
                    incident_point
                )

        self.outbound_waypoints = list(self.waypoints)
        self.returning = False
        self.waypoint_index = 0

        now = (
            self.get_clock()
            .now()
            .nanoseconds / 1e9
        )

        self.start_time = (
            now + START_DELAY_SEC
        )

        self.active = True

        self.get_logger().info(
            f"Selected route received: "
            f"{requested_route}"
        )

        if requested_route != route_name:
            self.get_logger().info(
                f"Mapped {requested_route} "
                f"-> {route_name}"
            )

        self.get_logger().info(
            f"Golf cart will start in "
            f"{START_DELAY_SEC:.1f}s"
        )

        self.get_logger().info(
            f"Waypoints: {self.waypoints}"
        )

    # ========================================================
    # Rescue return trigger
    # ========================================================

    def return_callback(self, msg):
        if not msg.data or self.active or self.returning or not self.selected_route:
            return

        # Return along the exact same selected route, then continue to the
        # First Aid Center stop defined by world_meta.json.
        rev = list(reversed(self.outbound_waypoints[:-1]))
        extra = [
            (float(p[0]), float(p[1]))
            for p in (self.meta.get("first_aid", {}).get("cart_route_enu") or [])
        ]
        self.waypoints = rev + [p for p in extra if not rev or p != rev[-1]]
        self.waypoint_index = 0
        self.returning = True

        now = self.get_clock().now().nanoseconds / 1e9
        self.start_time = now + 0.8
        self.active = True
        self.arrived_pub.publish(Bool(data=False))

        self.get_logger().info(
            f"Rescue complete -> RETURN on {self.selected_route}; waypoints={self.waypoints}"
        )

    # ========================================================
    # Route-name compatibility
    # ========================================================

    def resolve_route_name(
        self,
        requested
    ):

        # Exact match
        if requested in self.routes:
            return requested

        # Supports:
        # A_west  -> A_north
        # B_north -> B_center
        # C_east  -> C_south

        prefix = (
            requested.split("_")[0]
            .upper()
        )

        for route_name in self.routes:

            route_prefix = (
                route_name.split("_")[0]
                .upper()
            )

            if route_prefix == prefix:
                return route_name

        return None

    # ========================================================
    # Odometry
    # ========================================================

    def odom_callback(
        self,
        msg
    ):

        pose = msg.pose.pose

        self.x = pose.position.x
        self.y = pose.position.y

        self.yaw = quaternion_to_yaw(
            pose.orientation
        )

    # ========================================================
    # Controller
    # ========================================================

    def control_loop(self):

        if not self.active:
            return

        if (
            self.x is None or
            self.y is None or
            self.yaw is None
        ):
            return

        now = (
            self.get_clock()
            .now()
            .nanoseconds / 1e9
        )

        # Give drone a head start
        if now < self.start_time:
            self.stop_cart()
            return

        # ----------------------------------------------------
        # Finished
        # ----------------------------------------------------

        if self.waypoint_index >= len(
            self.waypoints
        ):
            self.finish_mission()
            return

        target_x, target_y = (
            self.waypoints[
                self.waypoint_index
            ]
        )

        dx = target_x - self.x
        dy = target_y - self.y

        distance = math.hypot(
            dx,
            dy
        )

        # ----------------------------------------------------
        # Waypoint reached
        # ----------------------------------------------------

        if distance < WAYPOINT_TOLERANCE:

            self.get_logger().info(
                f"Waypoint "
                f"{self.waypoint_index + 1}"
                f"/{len(self.waypoints)} reached"
            )

            self.waypoint_index += 1

            if self.waypoint_index >= len(
                self.waypoints
            ):
                self.finish_mission()

            return

        # ----------------------------------------------------
        # Heading
        # ----------------------------------------------------

        desired_yaw = math.atan2(
            dy,
            dx
        )

        yaw_error = normalize_angle(
            desired_yaw -
            self.yaw
        )

        # ----------------------------------------------------
        # Angular controller
        # ----------------------------------------------------

        angular = clamp(
            1.8 * yaw_error,
            -MAX_TURN_RATE,
            MAX_TURN_RATE
        )

        # ----------------------------------------------------
        # Linear controller
        # ----------------------------------------------------

        # إذا الاتجاه غلط كثير:
        # لف أول ثم امش
        if abs(yaw_error) > 0.8:

            linear = 0.0

        else:

            heading_factor = max(
                0.25,
                1.0 -
                abs(yaw_error)
            )

            linear = (
                MAX_SPEED *
                heading_factor
            )

            # قرب waypoint:
            # خفف السرعة
            if distance < 3.0:
                linear = min(
                    linear,
                    self.corner_speed()
                )

        # ----------------------------------------------------
        # Publish command
        # ----------------------------------------------------

        cmd = Twist()

        cmd.linear.x = float(
            linear
        )

        cmd.angular.z = float(
            angular
        )

        self.cmd_pub.publish(cmd)

    def corner_speed(self):
        """Speed cap near the current waypoint, from the turn the cart makes there.

        Crawling at 1 m/s into every waypoint (even a straight one) froze the drone's
        escort target, so the drone braked at each corner. Only the last waypoint stops.
        """
        i = self.waypoint_index
        if i + 1 >= len(self.waypoints):
            return 1.0
        prev = self.waypoints[i - 1] if i > 0 else (self.x, self.y)
        here = self.waypoints[i]
        nxt = self.waypoints[i + 1]
        turn = abs(normalize_angle(
            math.atan2(nxt[1] - here[1], nxt[0] - here[0]) -
            math.atan2(here[1] - prev[1], here[0] - prev[0])
        ))
        return MAX_SPEED * max(0.3, 1.0 - turn / (math.pi / 2))

    # ========================================================
    # Stop
    # ========================================================

    def stop_cart(self):

        cmd = Twist()

        cmd.linear.x = 0.0
        cmd.angular.z = 0.0

        self.cmd_pub.publish(cmd)

    # ========================================================
    # Finished
    # ========================================================

    def finish_mission(self):

        if not self.active:
            return

        self.stop_cart()
        self.active = False

        if self.returning:
            self.returning = False
            self.returned_pub.publish(Bool(data=True))
            self.get_logger().info("Golf cart returned to FIRST AID CENTER")
            return

        self.arrived_pub.publish(Bool(data=True))
        self.get_logger().info("Golf cart reached INCIDENT")


def main(args=None):

    rclpy.init(args=args)

    node = CartNode()

    try:

        rclpy.spin(node)

    except KeyboardInterrupt:

        pass

    finally:

        node.stop_cart()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()