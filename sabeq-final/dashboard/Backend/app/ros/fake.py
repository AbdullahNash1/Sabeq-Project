"""In-memory ROS graph for tests (no ROS needed): same publish/subscribe shape as rclpy.

FakeRosGraph carries rclpy-shaped messages (String.data, Bool.data, Image, Detection2DArray
with header.stamp) between participants, with DDS-like QoS matching: a BEST_EFFORT publisher
does not reach a RELIABLE subscriber, and a TRANSIENT_LOCAL publisher's last message is
delivered to late subscribers that ask for it. Deliveries are deferred with loop.call_soon (no
re-entrancy, like a real middleware). FakeTransport is the backend's view of the graph; test
code attaches the simulated nodes as other participants.
"""
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Callable

from app.ros.transport import Transport
from app.ros.wire import QoS, from_wire, to_wire


@dataclass
class String:
    data: str = ""


@dataclass
class Bool:
    data: bool = False


@dataclass
class Time:
    sec: int = 0
    nanosec: int = 0

    @classmethod
    def of(cls, t: float) -> "Time":
        sec = int(t)
        return cls(sec=sec, nanosec=int(round((t - sec) * 1e9)))


@dataclass
class Header:
    stamp: Time = field(default_factory=Time)
    frame_id: str = "camera_link"


@dataclass
class Image:
    header: Header
    height: int
    width: int
    encoding: str
    step: int
    data: bytes


@dataclass
class CompressedImage:
    header: Header
    format: str
    data: bytes


@dataclass
class Detection2DArray:
    header: Header
    detections: list


MSG_TYPES = SimpleNamespace(String=String, Bool=Bool)


@dataclass
class _Sub:
    topic: str
    qos: QoS
    callback: Callable   # receives the raw (rclpy-shaped) message
    owner: str


class FakeRosGraph:

    def __init__(self, loop=None, clock: Callable[[], float] = lambda: 0.0):
        self.loop = loop
        self.clock = clock
        self.subs: list = []
        self.published: list = []    # (owner, topic, message)
        self._latched: dict = {}
        self.publishers: dict = {}   # (owner, topic) -> QoS, for QoS assertions

    def subscribe(self, owner: str, topic: str, qos: QoS, callback: Callable) -> None:
        self.subs.append(_Sub(topic, qos, callback, owner))
        if qos.transient_local and topic in self._latched:
            self._deliver(callback, self._latched[topic])

    def publish(self, owner: str, topic: str, qos: QoS, msg: Any) -> None:
        self.publishers[(owner, topic)] = qos
        self.published.append((owner, topic, msg))
        if qos.transient_local:
            self._latched[topic] = msg
        for sub in self.subs:
            if sub.topic != topic:
                continue
            if sub.qos.reliable and not qos.reliable:
                continue  # DDS: a RELIABLE reader never matches a BEST_EFFORT writer
            self._deliver(sub.callback, msg)

    def _deliver(self, callback: Callable, msg: Any) -> None:
        if self.loop is not None:
            self.loop.call_soon(callback, msg)
        else:
            callback(msg)

    def on(self, topic: str) -> list:
        return [m for _, t, m in self.published if t == topic]


class FakeTransport(Transport):
    """The backend_bridge participant of a FakeRosGraph."""

    def __init__(self, graph: FakeRosGraph, name: str = "backend_bridge"):
        self.graph = graph
        self.name = name
        self.started = False
        self.subscriptions: dict = {}   # topic -> (kind, QoS)
        self.publishers: dict = {}

    def start(self, loop) -> None:
        if self.graph.loop is None:
            self.graph.loop = loop
        self.started = True

    def stop(self) -> None:
        self.started = False

    def subscribe(self, topic: str, kind: str, qos: QoS, callback: Callable) -> None:
        self.subscriptions.setdefault(topic, []).append((kind, qos))

        def on_message(msg):
            value = from_wire(kind, msg)
            if value is not None:
                callback(value)
        self.graph.subscribe(self.name, topic, qos, on_message)

    def publisher(self, topic: str, kind: str, qos: QoS) -> Callable:
        self.publishers[topic] = (kind, qos)
        return lambda payload: self.graph.publish(self.name, topic, qos,
                                                  to_wire(kind, payload, MSG_TYPES))

    def now(self) -> float:
        return self.graph.clock()
