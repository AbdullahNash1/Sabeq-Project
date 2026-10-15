"""vision_msgs/Detection2DArray -> API boxes (ROS_TOPICS.md §4.2, ros_samples.md §1).

Pure functions over "message-like" input: real rclpy messages (attribute access)
or plain dicts (tests, recorded samples). No ROS import needed.
"""
from typing import Any

from app.models import Box

FALLBACK_WIDTH = 1280
FALLBACK_HEIGHT = 960


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _clamp_pct(value: float) -> float:
    return min(100.0, max(0.0, value))


def stamp_to_sim_time(stamp: Any) -> float:
    """builtin_interfaces/Time (SIM time) -> simTimeS. Never convert to an ISO date."""
    return round(float(_get(stamp, "sec", 0)) + float(_get(stamp, "nanosec", 0)) * 1e-9, 9)


def detection_to_box(detection: Any, image_w: float, image_h: float) -> "Box | None":
    """One vision_msgs/Detection2D -> Box, or None if it has no results or no area in frame."""
    results = _get(detection, "results") or []
    if len(results) == 0:
        return None
    hypothesis = _get(results[0], "hypothesis")

    bbox = _get(detection, "bbox")
    center = _get(_get(bbox, "center"), "position")
    cx, cy = float(_get(center, "x")), float(_get(center, "y"))
    sx, sy = float(_get(bbox, "size_x")), float(_get(bbox, "size_y"))

    # Centre/size in pixels -> clamped edges in percent of the frame.
    left = _clamp_pct((cx - sx / 2) / image_w * 100)
    right = _clamp_pct((cx + sx / 2) / image_w * 100)
    top = _clamp_pct((cy - sy / 2) / image_h * 100)
    bottom = _clamp_pct((cy + sy / 2) / image_h * 100)
    if right <= left or bottom <= top:
        return None

    return Box(
        x=round(left, 2),
        y=round(top, 2),
        w=round(right - left, 2),
        h=round(bottom - top, 2),
        conf=round(float(_get(hypothesis, "score")), 3),
        cls=str(_get(hypothesis, "class_id")),
    )


def detections_to_boxes(
    msg: Any, image_w: "float | None" = None, image_h: "float | None" = None
) -> list:
    """Detection2DArray -> list[Box].

    image_w/image_h: size of the latest /camera/image; falls back to 1280x960.
    """
    w = image_w or FALLBACK_WIDTH
    h = image_h or FALLBACK_HEIGHT
    boxes = []
    for detection in _get(msg, "detections") or []:
        box = detection_to_box(detection, w, h)
        if box is not None:
            boxes.append(box)
    return boxes


def detections_sim_time(msg: Any) -> "float | None":
    """simTimeS from the array header (not the per-detection headers)."""
    stamp = _get(_get(msg, "header"), "stamp")
    return stamp_to_sim_time(stamp) if stamp is not None else None
