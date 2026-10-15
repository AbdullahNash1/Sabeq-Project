"""The backend's ROS transport: one rclpy node "backend_bridge" (ROS 2 Humble).

rclpy is imported lazily in start(), so the app starts on machines without ROS as long as no
SOURCE_*=ros part is used. The node spins in a MultiThreadedExecutor on a background thread;
every subscription callback converts its message (app/ros/wire.py) on that thread and hands
the value to the asyncio loop with loop.call_soon_threadsafe. Nothing here touches the store
or the WebSocket. Publishing (from the asyncio thread) is thread-safe in rclpy.
"""
import logging
import threading
from types import SimpleNamespace
from typing import Callable

from app.ros.wire import QoS, from_wire, to_wire

log = logging.getLogger(__name__)


class RosUnavailable(RuntimeError):
    pass


class Transport:
    """What RosContext needs from a ROS connection (real: RclpyTransport; tests: FakeTransport)."""

    def start(self, loop) -> None: ...

    def stop(self) -> None: ...

    def subscribe(self, topic: str, kind: str, qos: QoS, callback: Callable) -> None: ...

    def publisher(self, topic: str, kind: str, qos: QoS) -> Callable: ...

    def now(self) -> float: ...


class RclpyTransport(Transport):

    def __init__(self, node_name: str = "backend_bridge", use_sim_time: bool = True):
        self.node_name = node_name
        self.use_sim_time = use_sim_time
        self.node = None
        self._loop = None
        self._executor = None
        self._thread = None
        self._owns_context = False
        self._types = {}
        self._pubs = {}

    def start(self, loop) -> None:
        try:
            import rclpy
            from rclpy.executors import MultiThreadedExecutor
            from rclpy.parameter import Parameter
            from sensor_msgs.msg import CompressedImage, Image
            from std_msgs.msg import Bool, String
            from vision_msgs.msg import Detection2DArray
        except ImportError as exc:
            raise RosUnavailable(
                f"SOURCE_*=ros needs ROS 2 Humble ({exc}). Run `source /opt/ros/humble/setup.bash` "
                "and create the venv with --system-site-packages (see README.md).") from exc
        self._loop = loop
        self._types = {"string": String, "bool": Bool, "json": String, "image": Image,
                       "jpeg": CompressedImage, "detections": Detection2DArray}
        self._msg_types = SimpleNamespace(String=String, Bool=Bool)
        if not rclpy.ok():
            rclpy.init()
            self._owns_context = True
        self.node = rclpy.create_node(
            self.node_name,
            parameter_overrides=[Parameter("use_sim_time", Parameter.Type.BOOL, self.use_sim_time)])
        self._executor = MultiThreadedExecutor()
        self._executor.add_node(self.node)
        self._thread = threading.Thread(target=self._executor.spin, name="ros-backend-bridge",
                                        daemon=True)
        self._thread.start()
        log.info("ROS node %s started (use_sim_time=%s)", self.node_name, self.use_sim_time)

    def stop(self) -> None:
        if self._executor is not None:
            self._executor.shutdown()
        if self.node is not None:
            self.node.destroy_node()
            self.node = None
        if self._owns_context:
            import rclpy
            if rclpy.ok():
                rclpy.shutdown()
            self._owns_context = False

    def _qos(self, qos: QoS):
        from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy
        return QoSProfile(
            history=HistoryPolicy.KEEP_LAST, depth=qos.depth,
            reliability=ReliabilityPolicy.RELIABLE if qos.reliable else ReliabilityPolicy.BEST_EFFORT,
            durability=(DurabilityPolicy.TRANSIENT_LOCAL if qos.transient_local
                        else DurabilityPolicy.VOLATILE))

    def subscribe(self, topic: str, kind: str, qos: QoS, callback: Callable) -> None:
        loop = self._loop

        def on_message(msg):  # ROS executor thread
            try:
                value = from_wire(kind, msg)
            except Exception:  # noqa: BLE001
                log.exception("bad %s message on %s", kind, topic)
                return
            if value is not None:
                loop.call_soon_threadsafe(callback, value)
        self.node.create_subscription(self._types[kind], topic, on_message, self._qos(qos))

    def publisher(self, topic: str, kind: str, qos: QoS) -> Callable:
        key = (topic, kind)
        if key not in self._pubs:
            self._pubs[key] = self.node.create_publisher(self._types[kind], topic, self._qos(qos))
        pub = self._pubs[key]
        return lambda payload: pub.publish(to_wire(kind, payload, self._msg_types))

    def now(self) -> float:
        """ROS time in seconds (sim time with use_sim_time; 0 until /clock arrives)."""
        return self.node.get_clock().now().nanoseconds * 1e-9 if self.node is not None else 0.0
