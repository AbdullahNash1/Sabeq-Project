"""ROS message <-> plain value conversion, shared by the real rclpy transport and the fake bus.

Both transports deliver rclpy-shaped messages (attribute access: `.data`, `.header.stamp`,
`.width`, ...); these functions turn them into what the ROS sources use, on whatever thread
receives them, before the hand-off to asyncio. Kinds:

  string      std_msgs/String  <-> str                (/scan/active_route, /selected_route)
  bool        std_msgs/Bool    <-> bool               (/scan/complete)
  json        std_msgs/String  <-> dict (json.loads)  (every JSON topic of ROS_TOPICS.md)
  image       sensor_msgs/Image -> ImageFrame          (/camera/image)
  jpeg        sensor_msgs/CompressedImage -> ImageFrame, encoding "jpeg"  (/camera/image/compressed)
  detections  vision_msgs/Detection2DArray -> as is    (/person_detections; app/ros/convert.py)
"""
from dataclasses import dataclass
from io import BytesIO
import json
import logging
from typing import Any

from app.ros.convert import stamp_to_sim_time

log = logging.getLogger(__name__)

KINDS = ("string", "bool", "json", "image", "jpeg", "detections")


@dataclass(frozen=True)
class ImageFrame:
    """One /camera/image frame, copied off the ROS message (bytes are immutable)."""

    width: int
    height: int
    encoding: str
    step: int
    data: bytes
    sim_time_s: "float | None"
    seq: int = 0


@dataclass(frozen=True)
class QoS:
    """ROS_TOPICS.md §1 QoS: reliability, history depth, durability."""

    reliable: bool
    depth: int
    transient_local: bool = False


BEST_EFFORT_1 = QoS(False, 1)
BEST_EFFORT_5 = QoS(False, 5)
RELIABLE_10 = QoS(True, 10)
RELIABLE_20 = QoS(True, 20)
LATCHED_1 = QoS(True, 1, transient_local=True)

_seq = 0


def from_wire(kind: str, msg: Any) -> Any:
    """ROS message -> plain value; None when the message cannot be used."""
    global _seq
    if kind == "string":
        return str(msg.data)
    if kind == "bool":
        return bool(msg.data)
    if kind == "json":
        try:
            data = json.loads(msg.data)
        except (TypeError, ValueError) as exc:
            log.warning("ignoring non-JSON message: %s", exc)
            return None
        return data if isinstance(data, dict) else None
    if kind == "image":
        _seq += 1
        header = getattr(msg, "header", None)
        stamp = getattr(header, "stamp", None)
        return ImageFrame(
            width=int(msg.width), height=int(msg.height), encoding=str(msg.encoding),
            step=int(msg.step) or int(msg.width) * 3, data=bytes(msg.data),
            sim_time_s=stamp_to_sim_time(stamp) if stamp is not None else None, seq=_seq)
    if kind == "jpeg":
        if "jpeg" not in str(msg.format).lower():
            return None
        data = bytes(msg.data)
        try:
            from PIL import Image  # header parse only; the JPEG is streamed as is
            width, height = Image.open(BytesIO(data)).size
        except Exception:  # noqa: BLE001
            log.warning("ignoring unreadable compressed camera frame")
            return None
        _seq += 1
        header = getattr(msg, "header", None)
        stamp = getattr(header, "stamp", None)
        return ImageFrame(
            width=width, height=height, encoding="jpeg", step=0, data=data,
            sim_time_s=stamp_to_sim_time(stamp) if stamp is not None else None, seq=_seq)
    if kind == "detections":
        return msg
    raise ValueError(f"unknown message kind {kind!r}")


def to_wire(kind: str, payload: Any, types: Any) -> Any:
    """Plain value -> ROS message, built with `types.String` / `types.Bool`."""
    if kind == "json":
        return types.String(data=json.dumps(payload, ensure_ascii=False))
    if kind == "string":
        return types.String(data=str(payload))
    if kind == "bool":
        return types.Bool(data=bool(payload))
    raise ValueError(f"cannot publish message kind {kind!r}")
