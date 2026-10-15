#!/usr/bin/env python3
"""Visual rescue + automatic return trigger for the SABEQ Expo demo.

This node does not fake drone telemetry and does not replace the working mission logic.
It only animates Gazebo rescue models that are already part of expo_demo:
  medic_0, medic_1, stretcher, patient

Flow after /golf_cart/arrived=True:
  medics appear on the cart -> dismount with stretcher -> walk to patient -> treat ->
  patient is placed on stretcher -> medics carry stretcher/patient back to cart ->
  /rescue/return_start=True -> cart_node + mission_node return on the same route.

Inputs:
  /execution/selected_route  std_msgs/String
  /golf_cart/odom            nav_msgs/Odometry
  /golf_cart/arrived         std_msgs/Bool
  /golf_cart/returned        std_msgs/Bool

Outputs:
  /rescue/return_start       std_msgs/Bool
  /rescue/status             std_msgs/String JSON
"""

import json
import math
import shutil
import subprocess
from pathlib import Path

import rclpy
from nav_msgs.msg import Odometry
from rclpy.node import Node
from std_msgs.msg import Bool, String


META_PATH = Path.home() / "emergency_drone" / "world_meta.json"
BED_X = -2.3
BED_TOP = 0.72
MEDIC_RIDE = ((-1.55, 0.5), (-3.05, -0.5))
MEDIC_SPACING = 1.35
CARRY_Z = 0.45
STRETCHER_TOP = 0.36
WALK_SPEED = 4.0
DISMOUNT_TIME = 0.7
TREAT_TIME = 1.4
LOAD_TIME = 0.8
BOARD_TIME = 0.8


def yaw_from_quaternion(q):
    siny_cosp = 2.0 * (q.w * q.z + q.x * q.y)
    cosy_cosp = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
    return math.atan2(siny_cosp, cosy_cosp)


def cart_local(cart, lx, ly):
    c, s = math.cos(cart[2]), math.sin(cart[2])
    return cart[0] + c * lx - s * ly, cart[1] + s * lx + c * ly


def quat_from_rpy(roll, pitch, yaw):
    cr, sr = math.cos(roll / 2), math.sin(roll / 2)
    cp, sp = math.cos(pitch / 2), math.sin(pitch / 2)
    cy, sy = math.cos(yaw / 2), math.sin(yaw / 2)
    return (
        cr * cp * cy + sr * sp * sy,
        sr * cp * cy - cr * sp * sy,
        cr * sp * cy + sr * cp * sy,
        cr * cp * sy - sr * sp * cy,
    )


class RescueReturnNode(Node):
    def __init__(self):
        super().__init__("sabeq_rescue_return_node")
        self.declare_parameter("update_hz", 4.0)
        self.update_hz = max(2.0, min(6.0, float(self.get_parameter("update_hz").value)))

        if not META_PATH.is_file():
            raise RuntimeError(f"world metadata not found: {META_PATH}")
        if shutil.which("gz") is None:
            raise RuntimeError("Gazebo CLI `gz` was not found in PATH")

        self.meta = json.loads(META_PATH.read_text())
        self.world = self.meta.get("world", "expo_demo")
        rm = self.meta.get("rescue_models") or {}
        self.medics = list(rm.get("medics") or [])
        self.stretcher = rm.get("stretcher")
        self.patient = rm.get("patient")
        if len(self.medics) != 2 or not self.stretcher or not self.patient:
            raise RuntimeError("rescue_models in world_meta.json are incomplete")

        p = self.meta.get("patient_enu") or self.meta.get("incident_enu")
        self.patient_home = (float(p[0]), float(p[1]), float(p[2] if len(p) > 2 else 0.0))
        self.pose_service = f"/world/{self.world}/set_pose_vector"

        self.route = None
        c0 = self.meta.get("cart_start_enu") or [0.0, 0.0, 0.0]
        self.cart = (float(c0[0]), float(c0[1]), float(c0[2] if len(c0) > 2 else 0.0))
        self.state = "IDLE"
        self.state_t = 0.0
        self.last_clock = None
        self.center = self.patient_home[:2]
        self.center_yaw = 0.0
        self.side_point = None
        self.bed_point = None
        self.loaded = False
        self.gz_ok = None
        self.return_sent = False

        self.return_pub = self.create_publisher(Bool, "/rescue/return_start", 10)
        self.status_pub = self.create_publisher(String, "/rescue/status", 10)
        self.create_subscription(String, "/execution/selected_route", self.on_route, 10)
        self.create_subscription(Odometry, "/golf_cart/odom", self.on_odom, 10)
        self.create_subscription(Bool, "/golf_cart/arrived", self.on_arrived, 10)
        self.create_subscription(Bool, "/golf_cart/returned", self.on_returned, 10)
        self.create_timer(1.0 / self.update_hz, self.tick)
        self.create_timer(1.0, self.publish_status)

        self.get_logger().info(
            f"Rescue visualizer ready | medics={self.medics} | stretcher={self.stretcher} | "
            f"patient={self.patient} | {self.update_hz:.1f} Hz"
        )

    def on_route(self, msg):
        if msg.data.strip():
            self.route = msg.data.strip()

    def on_odom(self, msg):
        p = msg.pose.pose.position
        self.cart = (float(p.x), float(p.y), yaw_from_quaternion(msg.pose.pose.orientation))

    def on_arrived(self, msg):
        if not msg.data or self.state not in ("IDLE", "DONE"):
            return
        self.loaded = False
        self.return_sent = False
        self.state = "ON_CART"
        self.state_t = 0.0
        self.get_logger().info("Cart arrived -> two medics preparing to dismount")

    def on_returned(self, msg):
        if msg.data:
            self.state = "DONE"
            self.state_t = 0.0
            self.get_logger().info("Cart + patient returned to First Aid Center")

    def sim_dt(self):
        now = self.get_clock().now().nanoseconds * 1e-9
        if self.last_clock is None:
            self.last_clock = now
            return 0.0
        dt = max(0.0, min(now - self.last_clock, 0.5))
        self.last_clock = now
        return dt

    def request_text(self, poses):
        chunks = []
        for name, x, y, yaw, z, roll, pitch in poses:
            qw, qx, qy, qz = quat_from_rpy(roll, pitch, yaw)
            safe = name.replace('\\', '\\\\').replace('"', '\\"')
            chunks.append(
                "pose { "
                f'name: "{safe}" '
                f"position {{ x: {x:.6f} y: {y:.6f} z: {z:.6f} }} "
                f"orientation {{ w: {qw:.9f} x: {qx:.9f} y: {qy:.9f} z: {qz:.9f} }} "
                "}"
            )
        return " ".join(chunks)

    def send(self, poses):
        if not poses:
            return True
        cmd = [
            "gz", "service", "-s", self.pose_service,
            "--reqtype", "gz.msgs.Pose_V", "--reptype", "gz.msgs.Boolean",
            "--timeout", "1200", "--req", self.request_text(poses),
        ]
        try:
            p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, timeout=2.0, check=False)
            self.gz_ok = p.returncode == 0
            if not self.gz_ok:
                self.get_logger().warning((p.stderr or p.stdout or "pose update failed")[:180])
            return self.gz_ok
        except (OSError, subprocess.TimeoutExpired) as exc:
            self.gz_ok = False
            self.get_logger().warning(f"Gazebo rescue pose update failed: {exc}")
            return False

    def walking_formation(self, center, yaw, z, loaded, travel=0.0):
        ux, uy = math.cos(yaw), math.sin(yaw)
        phase = travel * math.pi / 0.75
        bob = 0.04 * abs(math.sin(phase))
        roll = math.radians(3.0) * math.sin(phase)
        pitch = math.radians(5.0)
        poses = []
        for name, k in zip(self.medics, (1.0, -1.0)):
            x = center[0] + ux * k * MEDIC_SPACING
            y = center[1] + uy * k * MEDIC_SPACING
            poses.append((name, x, y, yaw, bob, roll, pitch))
        poses.append((self.stretcher, center[0], center[1], yaw, z, 0.0, 0.0))
        if loaded:
            poses.append((self.patient, center[0], center[1], yaw,
                          z + STRETCHER_TOP, 0.0, 0.0))
        return poses

    def riding_poses(self):
        poses = []
        for name, (lx, ly) in zip(self.medics, MEDIC_RIDE):
            x, y = cart_local(self.cart, lx, ly)
            poses.append((name, x, y, self.cart[2], BED_TOP, 0.0, 0.0))
        sx, sy = cart_local(self.cart, BED_X, 0.0)
        poses.append((self.stretcher, sx, sy, self.cart[2], BED_TOP, 0.0, 0.0))
        if self.loaded:
            poses.append((self.patient, sx, sy, self.cart[2],
                          BED_TOP + STRETCHER_TOP, 0.0, 0.0))
        return poses

    @staticmethod
    def move_toward(pos, target, max_step):
        dx, dy = target[0] - pos[0], target[1] - pos[1]
        d = math.hypot(dx, dy)
        if d <= max_step or d < 1e-6:
            return target, d, True
        return (pos[0] + dx / d * max_step, pos[1] + dy / d * max_step), max_step, False

    def prepare_ground_path(self):
        bed = cart_local(self.cart, BED_X, 0.0)
        left = (-math.sin(self.cart[2]), math.cos(self.cart[2]))
        # Pick the cart side closer to the patient.
        to_p = (self.patient_home[0] - bed[0], self.patient_home[1] - bed[1])
        side = 1.0 if to_p[0] * left[0] + to_p[1] * left[1] >= 0 else -1.0
        self.bed_point = bed
        self.side_point = (bed[0] + left[0] * side * 2.0,
                           bed[1] + left[1] * side * 2.0)
        self.center = bed
        self.center_yaw = self.cart[2]

    def tick(self):
        dt = self.sim_dt()
        if dt <= 0.0 or self.state == "IDLE":
            return
        self.state_t += dt

        if self.state == "ON_CART":
            self.send(self.riding_poses())
            if self.state_t >= BOARD_TIME:
                self.prepare_ground_path()
                self.state, self.state_t = "DISMOUNT", 0.0
                self.get_logger().info("Medics dismounting with stretcher")
            return

        if self.state == "DISMOUNT":
            f = min(1.0, self.state_t / DISMOUNT_TIME)
            sx, sy = self.bed_point
            tx, ty = self.side_point
            self.center = (sx + (tx - sx) * f, sy + (ty - sy) * f)
            self.center_yaw = math.atan2(self.patient_home[1] - self.center[1],
                                         self.patient_home[0] - self.center[0])
            z = BED_TOP + (CARRY_Z - BED_TOP) * f
            self.send(self.walking_formation(self.center, self.center_yaw, z, False, self.state_t * 2.0))
            if f >= 1.0:
                self.state, self.state_t = "TO_PATIENT", 0.0
            return

        if self.state == "TO_PATIENT":
            px, py = self.patient_home[:2]
            # Stop the stretcher just beside the injured visitor rather than on top of them.
            target = (px + 1.0, py)
            new, moved, done = self.move_toward(self.center, target, WALK_SPEED * dt)
            if moved > 1e-6:
                self.center_yaw = math.atan2(new[1] - self.center[1], new[0] - self.center[0])
            self.center = new
            self.send(self.walking_formation(self.center, self.center_yaw, CARRY_Z, False, self.state_t * WALK_SPEED))
            if done:
                self.state, self.state_t = "TREAT", 0.0
                self.get_logger().info("Medics reached patient; treatment started")
            return

        if self.state == "TREAT":
            self.send(self.walking_formation(self.center, self.center_yaw, 0.12, False))
            if self.state_t >= TREAT_TIME:
                self.state, self.state_t = "LOAD", 0.0
                self.get_logger().info("Loading patient onto stretcher")
            return

        if self.state == "LOAD":
            # Patient visibly appears on top of the stretcher during this beat.
            loaded_now = self.state_t >= LOAD_TIME * 0.45
            poses = self.walking_formation(self.center, self.center_yaw, CARRY_Z, loaded_now)
            self.send(poses)
            if self.state_t >= LOAD_TIME:
                self.loaded = True
                self.state, self.state_t = "TO_CART", 0.0
                self.get_logger().info("Patient loaded; carrying back to cart")
            return

        if self.state == "TO_CART":
            new, moved, done = self.move_toward(self.center, self.side_point, WALK_SPEED * dt)
            if moved > 1e-6:
                self.center_yaw = math.atan2(new[1] - self.center[1], new[0] - self.center[0])
            self.center = new
            self.send(self.walking_formation(self.center, self.center_yaw, CARRY_Z, True,
                                             self.state_t * WALK_SPEED))
            if done:
                self.state, self.state_t = "BOARDING", 0.0
            return

        if self.state == "BOARDING":
            self.send(self.riding_poses())
            if self.state_t >= BOARD_TIME and not self.return_sent:
                self.return_sent = True
                self.state, self.state_t = "RIDING_BACK", 0.0
                self.publish_status()
                self.return_pub.publish(Bool(data=True))
                self.get_logger().info("Patient secured on cart -> automatic return started")
            return

        if self.state == "RIDING_BACK":
            # Keep medics, stretcher and patient physically attached to the moving cart.
            self.send(self.riding_poses())
            return

        if self.state == "DONE":
            self.send(self.riding_poses())

    def publish_status(self):
        self.status_pub.publish(String(data=json.dumps({
            "state": self.state,
            "route": self.route,
            "patientLoaded": self.loaded,
            "returnStarted": self.return_sent,
            "gazeboPoseOk": self.gz_ok,
        }, separators=(",", ":"))))


def main(args=None):
    rclpy.init(args=args)
    node = None
    try:
        node = RescueReturnNode()
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
