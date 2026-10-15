#!/usr/bin/env python3
"""
pose_sender.py - moves models in Gazebo (golf cart, people, medics) for sim_node.py.

Started automatically by sim_node.py as a separate process, so the blocking
Gazebo service calls never freeze the camera window or YOLO.
You do not run this yourself.

Receives JSON pose lists on UDP 127.0.0.1:POSE_PORT:
  [["golf_cart", x, y, yaw], ["stretcher", x, y, yaw, z], ["person_x", x, y, yaw, z, roll, pitch], ...]
z, roll and pitch are optional (default 0). Only the newest message is applied; older ones are dropped.
"""
import json
import math
import os
import socket
import sys

from gz.transport13 import Node
from gz.msgs10.pose_v_pb2 import Pose_V
from gz.msgs10.boolean_pb2 import Boolean

import demo_common as dc


def main():
    world = sys.argv[1] if len(sys.argv) > 1 else "expo_demo"
    srv = f"/world/{world}/set_pose_vector"
    parent = os.getppid()

    node = Node()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", dc.POSE_PORT))
    sock.settimeout(1.0)
    warned = False

    while True:
        if os.getppid() != parent:      # sim_node.py exited -> exit too
            return
        try:
            data, _ = sock.recvfrom(65535)
        except socket.timeout:
            continue
        # drop anything older, keep only the newest poses
        sock.setblocking(False)
        try:
            while True:
                data, _ = sock.recvfrom(65535)
        except BlockingIOError:
            pass
        sock.settimeout(1.0)

        msg = Pose_V()
        for item in json.loads(data):
            name, x, y, yaw = item[:4]
            z = item[4] if len(item) > 4 else 0.0
            p = msg.pose.add()
            p.name = name
            p.position.x, p.position.y, p.position.z = x, y, z
            if len(item) > 6:                      # walking people: sway (roll) and lean (pitch)
                r, pt = item[5], item[6]
                cr, sr = math.cos(r / 2), math.sin(r / 2)
                cp, sp = math.cos(pt / 2), math.sin(pt / 2)
                cy, sy = math.cos(yaw / 2), math.sin(yaw / 2)
                p.orientation.w = cr * cp * cy + sr * sp * sy
                p.orientation.x = sr * cp * cy - cr * sp * sy
                p.orientation.y = cr * sp * cy + sr * cp * sy
                p.orientation.z = cr * cp * sy - sr * sp * cy
            else:
                p.orientation.z = math.sin(yaw / 2)
                p.orientation.w = math.cos(yaw / 2)
        try:
            node.request(srv, msg, Pose_V, Boolean, 1000)
        except Exception as e:  # noqa: BLE001
            if not warned:
                print(f"[pose_sender] set_pose_vector failed: {e}")
                warned = True


if __name__ == "__main__":
    main()
