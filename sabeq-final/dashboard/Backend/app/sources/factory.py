"""Build the SourceSet from SOURCE_CAMERA / SOURCE_ROUTE / SOURCE_DRONE / SOURCE_CART.

The ROS parts share one RosContext (one backend_bridge node, one command publisher, one ack
gate). rclpy is only imported when a ROS source starts, so all-mock runs need no ROS.
"""
from app.ros.context import RosContext
from app.sources.base import SourceSet
from app.sources.mock.camera import MockCameraSource
from app.sources.mock.cart import MockCartSource
from app.sources.mock.drone import MockDroneSource
from app.sources.mock.route import MockRouteSource
from app.sources.ros.camera import RosCameraSource
from app.sources.ros.cart import RosCartSource
from app.sources.ros.drone import RosDroneSource
from app.sources.ros.route import RosRouteSource

_CLASSES = {
    "camera": {"mock": MockCameraSource, "ros": RosCameraSource},
    "route": {"mock": MockRouteSource, "ros": RosRouteSource},
    "drone": {"mock": MockDroneSource, "ros": RosDroneSource},
    "cart": {"mock": MockCartSource, "ros": RosCartSource},
}
PARTS = ("camera", "route", "drone", "cart")


def build_sources(settings, service, store, ros_transport=None) -> SourceSet:
    """ros_transport: a Transport to use instead of rclpy (tests: app.ros.fake.FakeTransport)."""
    ctx = None
    if any(getattr(settings, f"source_{part}") == "ros" for part in PARTS):
        ctx = RosContext(settings, service, ros_transport)

    def make(part: str):
        mode = getattr(settings, f"source_{part}")
        if mode == "ros":
            return _CLASSES[part]["ros"](service, store, settings, ctx)
        return _CLASSES[part][mode](service, store, settings)

    sources = SourceSet(*(make(part) for part in PARTS))
    sources.ros = ctx
    return sources
