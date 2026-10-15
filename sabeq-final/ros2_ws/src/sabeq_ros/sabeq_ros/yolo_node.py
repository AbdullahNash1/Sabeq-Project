import cv2
import rclpy

from cv_bridge import CvBridge
from rclpy.node import Node
from rclpy.qos import (
    QoSProfile,
    ReliabilityPolicy,
    HistoryPolicy,
    DurabilityPolicy,
)
from sensor_msgs.msg import Image
from vision_msgs.msg import (
    Detection2D,
    Detection2DArray,
    ObjectHypothesisWithPose,
)

from sabeq_ros.person_detector import PersonDetector, limit_cpu_threads


class YoloNode(Node):

    def __init__(self):
        super().__init__("yolo_node")

        # -------------------------------------------------
        # Parameters
        # -------------------------------------------------

        self.declare_parameter("model_path", "")
        self.declare_parameter("confidence", 0.30)
        self.declare_parameter("image_size", 1280)
        self.declare_parameter("max_detections", 500)
        self.declare_parameter("publish_annotated", True)

        # Percentage of the image width used for route counting.
        # 0.50 = middle 50% of the image.
        self.declare_parameter("center_roi_ratio", 0.50)

        # CPU only: torch threads for YOLO (0 = half the cores) so Gazebo keeps rendering.
        self.declare_parameter("cpu_threads", 0)

        model_path = (
            self.get_parameter("model_path")
            .get_parameter_value()
            .string_value
        )

        confidence = (
            self.get_parameter("confidence")
            .get_parameter_value()
            .double_value
        )

        image_size = (
            self.get_parameter("image_size")
            .get_parameter_value()
            .integer_value
        )

        max_detections = (
            self.get_parameter("max_detections")
            .get_parameter_value()
            .integer_value
        )

        self.publish_annotated = (
            self.get_parameter("publish_annotated")
            .get_parameter_value()
            .bool_value
        )

        self.center_roi_ratio = (
            self.get_parameter("center_roi_ratio")
            .get_parameter_value()
            .double_value
        )

        if not model_path:
            raise ValueError(
                "model_path parameter is required"
            )

        # -------------------------------------------------
        # YOLO
        # -------------------------------------------------

        device = limit_cpu_threads(
            self.get_parameter("cpu_threads").get_parameter_value().integer_value
        )
        self.get_logger().info(f"YOLO device: {device}")

        self.detector = PersonDetector(
            model_path=model_path,
            confidence=confidence,
            image_size=image_size,
            max_detections=max_detections,
        )

        self.bridge = CvBridge()

        # -------------------------------------------------
        # QoS
        # -------------------------------------------------

        image_qos = QoSProfile(
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
            reliability=ReliabilityPolicy.BEST_EFFORT,
            durability=DurabilityPolicy.VOLATILE,
        )

        # -------------------------------------------------
        # Subscriber
        # -------------------------------------------------

        self.subscription = self.create_subscription(
            Image,
            "/camera/image",
            self.image_callback,
            image_qos,
        )

        # -------------------------------------------------
        # Publishers
        # -------------------------------------------------

        # All people visible in the full image.
        self.detection_publisher = self.create_publisher(
            Detection2DArray,
            "/person_detections",
            10,
        )

        # Only people inside the center route ROI.
        self.center_detection_publisher = self.create_publisher(
            Detection2DArray,
            "/person_detections_center",
            10,
        )

        self.image_publisher = self.create_publisher(
            Image,
            "/yolo/annotated_image",
            image_qos,
        )

        self.get_logger().info(
            "YOLO node started | "
            f"conf={confidence:.2f} | "
            f"imgsz={image_size} | "
            f"center_roi={self.center_roi_ratio:.2f} | annotated={self.publish_annotated}"
        )

    # =====================================================
    # Detection message helper
    # =====================================================

    @staticmethod
    def create_detection_message(
        header,
        x1,
        y1,
        x2,
        y2,
        confidence,
    ):
        detection = Detection2D()
        detection.header = header

        detection.bbox.center.position.x = (
            x1 + x2
        ) / 2.0

        detection.bbox.center.position.y = (
            y1 + y2
        ) / 2.0

        detection.bbox.center.theta = 0.0

        detection.bbox.size_x = x2 - x1
        detection.bbox.size_y = y2 - y1

        hypothesis = ObjectHypothesisWithPose()

        hypothesis.hypothesis.class_id = "person"
        hypothesis.hypothesis.score = confidence

        detection.results.append(
            hypothesis
        )

        return detection

    # =====================================================
    # Camera callback
    # =====================================================

    def image_callback(self, message):

        frame = self.bridge.imgmsg_to_cv2(
            message,
            desired_encoding="bgr8",
        )

        detections = self.detector.detect(
            frame
        )

        height, width = frame.shape[:2]

        # -------------------------------------------------
        # Center ROI
        # -------------------------------------------------

        roi_width = (
            width * self.center_roi_ratio
        )

        roi_left = int(
            (width - roi_width) / 2
        )

        roi_right = int(
            (width + roi_width) / 2
        )

        all_detection_array = Detection2DArray()
        all_detection_array.header = message.header

        center_detection_array = Detection2DArray()
        center_detection_array.header = message.header

        annotated_frame = frame.copy() if self.publish_annotated else None

        center_count = 0

        # Draw ROI boundaries only when the optional annotated topic is requested.
        if self.publish_annotated:
            cv2.line(annotated_frame, (roi_left, 0), (roi_left, height), (0, 255, 255), 3)
            cv2.line(annotated_frame, (roi_right, 0), (roi_right, height), (0, 255, 255), 3)

        # -------------------------------------------------
        # Build detections
        # -------------------------------------------------

        for item in detections:

            x1, y1, x2, y2 = item["bbox"]
            confidence = item["confidence"]

            detection = (
                self.create_detection_message(
                    message.header,
                    x1,
                    y1,
                    x2,
                    y2,
                    confidence,
                )
            )

            # Full-frame detections
            all_detection_array.detections.append(
                detection
            )

            bbox_center_x = (
                x1 + x2
            ) / 2.0

            inside_center_roi = (
                roi_left
                <= bbox_center_x
                <= roi_right
            )

            if inside_center_roi:

                center_detection_array.detections.append(
                    detection
                )

                center_count += 1

                box_color = (
                    0,
                    255,
                    0,
                )

            else:

                box_color = (
                    180,
                    180,
                    180,
                )

            if self.publish_annotated:
                cv2.rectangle(
                    annotated_frame,
                    (int(x1), int(y1)),
                    (int(x2), int(y2)),
                    box_color,
                    3,
                )
                cv2.putText(
                    annotated_frame,
                    f"{confidence:.2f}",
                    (int(x1), max(int(y1) - 5, 15)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.6,
                    box_color,
                    2,
                )

        # -------------------------------------------------
        # Publish detections (always) + optional annotated image
        # -------------------------------------------------

        self.detection_publisher.publish(all_detection_array)
        self.center_detection_publisher.publish(center_detection_array)

        if self.publish_annotated:
            cv2.putText(
                annotated_frame,
                f"All: {len(detections)}",
                (30, 45),
                cv2.FONT_HERSHEY_SIMPLEX,
                1.0,
                (0, 255, 0),
                2,
            )
            cv2.putText(
                annotated_frame,
                f"Route ROI: {center_count}",
                (30, 85),
                cv2.FONT_HERSHEY_SIMPLEX,
                1.0,
                (0, 255, 255),
                2,
            )
            annotated_message = self.bridge.cv2_to_imgmsg(annotated_frame, encoding="bgr8")
            annotated_message.header = message.header
            self.image_publisher.publish(annotated_message)



def main(args=None):

    rclpy.init(args=args)

    node = YoloNode()

    try:
        rclpy.spin(node)

    except KeyboardInterrupt:
        pass

    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()