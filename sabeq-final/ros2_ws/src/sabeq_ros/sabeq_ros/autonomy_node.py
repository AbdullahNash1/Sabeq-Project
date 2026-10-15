import json
import math
import os
import statistics

import rclpy
import yaml

from ament_index_python.packages import (
    get_package_share_directory,
)
from rclpy.node import Node
from std_msgs.msg import Bool, String
from vision_msgs.msg import Detection2DArray


class AutonomyNode(Node):

    def __init__(self):
        super().__init__("autonomy_node")

        # -------------------------------------------------
        # Configuration
        # -------------------------------------------------

        package_share = get_package_share_directory(
            "sabeq_ros"
        )

        routes_file = os.path.join(
            package_share,
            "config",
            "routes.yaml",
        )

        with open(routes_file, "r") as file:
            config = yaml.safe_load(file)

        self.routes = config["routes"]
        self.scan_order = config["scan_order"]

        self.vehicle_speed = float(
            config.get(
                "vehicle_speed_mps",
                3.0,
            )
        )

        self.delay_per_person = float(
            config.get(
                "delay_per_person_s",
                0.5,
            )
        )

        for route_name, route_data in self.routes.items():

            route_data["distance"] = (
                self.calculate_distance(
                    route_data["waypoints"]
                )
            )

        # -------------------------------------------------
        # Scan state
        # -------------------------------------------------

        self.active_route = None
        self.scan_in_progress = False

        self.scanned_routes = set()

        self.route_counts = {
            route_name: 0
            for route_name in self.routes
        }

        # Store every frame count during each route scan.
        self.route_samples = {
            route_name: []
            for route_name in self.routes
        }

        # -------------------------------------------------
        # Subscribers
        # -------------------------------------------------

        # IMPORTANT:
        # Node 2 now uses only detections inside
        # the center route ROI.
        self.detection_subscription = self.create_subscription(
            Detection2DArray,
            "/person_detections_center",
            self.detection_callback,
            10,
        )

        self.route_subscription = self.create_subscription(
            String,
            "/scan/active_route",
            self.active_route_callback,
            10,
        )

        self.scan_complete_subscription = (
            self.create_subscription(
                Bool,
                "/scan/complete",
                self.scan_complete_callback,
                10,
            )
        )

        # -------------------------------------------------
        # Publishers
        # -------------------------------------------------

        self.selected_route_publisher = (
            self.create_publisher(
                String,
                "/selected_route",
                10,
            )
        )

        self.route_scores_publisher = (
            self.create_publisher(
                String,
                "/route_scores",
                10,
            )
        )

        # -------------------------------------------------
        # Startup
        # -------------------------------------------------

        self.get_logger().info(
            "SABEQ Autonomy Node started"
        )

        self.get_logger().info(
            "Crowd measurement: CENTER ROI + MEDIAN"
        )

        for route_name, route_data in self.routes.items():

            self.get_logger().info(
                f"{route_name}: "
                f"{route_data['distance']:.2f} m"
            )

    # =====================================================
    # Helpers
    # =====================================================

    @staticmethod
    def calculate_distance(waypoints):

        total_distance = 0.0

        for index in range(
            len(waypoints) - 1
        ):

            x1, y1 = waypoints[index]
            x2, y2 = waypoints[index + 1]

            total_distance += math.hypot(
                x2 - x1,
                y2 - y1,
            )

        return total_distance

    def reset_scan(self):

        self.route_counts = {
            route_name: 0
            for route_name in self.routes
        }

        self.route_samples = {
            route_name: []
            for route_name in self.routes
        }

        self.scanned_routes.clear()

        self.active_route = None
        self.scan_in_progress = True

    def finalize_route(self, route_name):

        if route_name is None:
            return

        samples = self.route_samples[
            route_name
        ]

        if not samples:

            self.route_counts[
                route_name
            ] = 0

            self.get_logger().warning(
                f"{route_name}: no detection samples"
            )

            return

        # Median removes occasional false-positive spikes
        # and occasional low-detection frames.
        stable_count = int(
            round(
                statistics.median(
                    samples
                )
            )
        )

        self.route_counts[
            route_name
        ] = stable_count

        self.get_logger().info(
            f"{route_name} FINAL | "
            f"People={stable_count} | "
            f"Samples={len(samples)} | "
            f"Min={min(samples)} | "
            f"Max={max(samples)}"
        )

    # =====================================================
    # Callbacks
    # =====================================================

    def active_route_callback(
        self,
        message,
    ):

        route_name = (
            message.data.strip()
        )

        # Empty route means Node 3 is rotating.
        if route_name == "":

            if self.active_route is not None:

                self.finalize_route(
                    self.active_route
                )

            self.active_route = None

            return

        if route_name not in self.routes:

            self.get_logger().warning(
                f"Unknown route: {route_name}"
            )

            return

        # First valid route starts a new scan.
        if not self.scan_in_progress:

            self.reset_scan()

            self.get_logger().info(
                "Scan started"
            )

        # Safety if route changes directly without blank.
        if (
            self.active_route is not None
            and self.active_route != route_name
        ):

            self.finalize_route(
                self.active_route
            )

        self.active_route = route_name

        self.scanned_routes.add(
            route_name
        )

        self.get_logger().info(
            f"Scanning: {route_name}"
        )

    def detection_callback(
        self,
        message,
    ):

        if not self.scan_in_progress:
            return

        if self.active_route is None:
            return

        current_count = len(
            message.detections
        )

        self.route_samples[
            self.active_route
        ].append(
            current_count
        )

    def scan_complete_callback(
        self,
        message,
    ):

        if not message.data:
            return

        if not self.scan_in_progress:

            self.get_logger().warning(
                "Received scan complete while "
                "no scan is active"
            )

            return

        # Finalize route if Node 3 did not send blank first.
        if self.active_route is not None:

            self.finalize_route(
                self.active_route
            )

            self.active_route = None

        missing_routes = [
            route_name
            for route_name in self.scan_order
            if route_name
            not in self.scanned_routes
        ]

        if missing_routes:

            self.get_logger().error(
                "Cannot select route. Missing scans: "
                + ", ".join(
                    missing_routes
                )
            )

            self.scan_in_progress = False

            return

        self.get_logger().info(
            "Scan complete"
        )

        # -------------------------------------------------
        # Select least crowded route.
        # Shortest distance is tie-breaker.
        # -------------------------------------------------

        best_route = None
        best_people = float("inf")
        best_distance = float("inf")

        scores = {}

        for route_name in self.scan_order:

            route_data = self.routes[
                route_name
            ]

            distance = route_data[
                "distance"
            ]

            people = self.route_counts[
                route_name
            ]

            travel_time = (
                distance
                / self.vehicle_speed
            )

            crowd_delay = (
                people
                * self.delay_per_person
            )

            estimated_time = (
                travel_time
                + crowd_delay
            )

            scores[route_name] = {
                "distanceM": round(
                    distance,
                    2,
                ),
                "peopleCount": people,
                "samples": len(
                    self.route_samples[
                        route_name
                    ]
                ),
                "travelTimeS": round(
                    travel_time,
                    2,
                ),
                "crowdDelayS": round(
                    crowd_delay,
                    2,
                ),
                "estimatedTimeS": round(
                    estimated_time,
                    2,
                ),
            }

            if (
                people < best_people
                or (
                    people == best_people
                    and distance
                    < best_distance
                )
            ):

                best_people = people
                best_distance = distance
                best_route = route_name

        # -------------------------------------------------
        # Publish scores
        # -------------------------------------------------

        scores_message = String()

        scores_message.data = json.dumps(
            scores,
            separators=(",", ":"),
        )

        self.route_scores_publisher.publish(
            scores_message
        )

        # -------------------------------------------------
        # Publish selected route
        # -------------------------------------------------

        selected_message = String()
        selected_message.data = best_route

        self.selected_route_publisher.publish(
            selected_message
        )

        self.get_logger().info(
            f"Counts: {self.route_counts}"
        )

        self.get_logger().info(
            f"Best Route: {best_route} | "
            f"People: {best_people} | "
            f"Distance: "
            f"{best_distance:.2f} m"
        )

        self.scan_in_progress = False
        self.active_route = None


def main(args=None):

    rclpy.init(args=args)

    node = AutonomyNode()

    try:
        rclpy.spin(node)

    except KeyboardInterrupt:
        pass

    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()