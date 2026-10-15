"""Detection2DArray -> API boxes, using the real sample from ros_samples.md §1/§2."""
from types import SimpleNamespace as NS

import pytest

from app.ros.convert import (
    detection_to_box,
    detections_sim_time,
    detections_to_boxes,
    stamp_to_sim_time,
)


def _detection(cx, cy, sx, sy, score=0.8059124946594238, class_id="person"):
    """Shape of a vision_msgs/Detection2D as echoed in ros_samples.md §2."""
    return {
        "header": {"stamp": {"sec": 44, "nanosec": 784000000}, "frame_id": "camera_link"},
        "results": [
            {
                "hypothesis": {"class_id": class_id, "score": score},
                "pose": {"pose": {"position": {"x": 0.0, "y": 0.0, "z": 0.0}}},
            }
        ],
        "bbox": {
            "center": {"position": {"x": cx, "y": cy}, "theta": 0.0},
            "size_x": sx,
            "size_y": sy,
        },
        "id": "",
    }


# Real values from ros_samples.md §1/§2
SAMPLE = _detection(1003.880615234375, 603.9335327148438, 12.593017578125, 45.8818359375)
SAMPLE_ARRAY = {
    "header": {"stamp": {"sec": 44, "nanosec": 784000000}, "frame_id": "camera_link"},
    "detections": [SAMPLE],
}


def _to_ns(value):
    """Turn the dict sample into attribute-style objects, like an rclpy message."""
    if isinstance(value, dict):
        return NS(**{k: _to_ns(v) for k, v in value.items()})
    if isinstance(value, list):
        return [_to_ns(v) for v in value]
    return value


def _expected_sample(box):
    assert box.x == 77.94
    assert box.y == 60.52
    assert box.w == 0.98
    assert box.h == 4.78
    assert box.conf == 0.806
    assert box.cls == "person"


def test_real_sample_dict():
    boxes = detections_to_boxes(SAMPLE_ARRAY, 1280, 960)
    assert len(boxes) == 1
    _expected_sample(boxes[0])


def test_real_sample_attribute_objects():
    boxes = detections_to_boxes(_to_ns(SAMPLE_ARRAY), 1280, 960)
    assert len(boxes) == 1
    _expected_sample(boxes[0])


def test_falls_back_to_1280x960_without_image_size():
    _expected_sample(detections_to_boxes(SAMPLE_ARRAY)[0])
    _expected_sample(detections_to_boxes(SAMPLE_ARRAY, None, None)[0])


def test_uses_latest_image_size():
    # Same pixels on a 2560x1920 frame: every percentage halves.
    box = detections_to_boxes(SAMPLE_ARRAY, 2560, 1920)[0]
    assert box.x == pytest.approx(77.94 / 2, abs=0.01)
    assert box.y == pytest.approx(60.52 / 2, abs=0.01)
    assert box.w == pytest.approx(0.49, abs=0.01)
    assert box.h == pytest.approx(2.39, abs=0.01)


def test_api_json_has_no_route_field():
    dumped = detections_to_boxes(SAMPLE_ARRAY)[0].model_dump(mode="json")
    assert dumped == {"x": 77.94, "y": 60.52, "w": 0.98, "h": 4.78, "conf": 0.806, "cls": "person"}


def test_box_partly_outside_is_clamped():
    box = detection_to_box(_detection(5, 950, 40, 40), 1280, 960)
    assert box.x == 0.0
    assert box.y == pytest.approx((950 - 20) / 960 * 100, abs=0.01)
    assert box.y + box.h == pytest.approx(100.0, abs=0.01)
    assert box.w == pytest.approx(25 / 1280 * 100, abs=0.01)


def test_box_fully_outside_is_skipped():
    assert detection_to_box(_detection(-100, -100, 10, 10), 1280, 960) is None


def test_detection_without_results_is_skipped():
    empty = dict(SAMPLE, results=[])
    assert detections_to_boxes({"detections": [empty, SAMPLE]}, 1280, 960)[0].x == 77.94
    assert len(detections_to_boxes({"detections": [empty]}, 1280, 960)) == 0


def test_empty_array():
    assert detections_to_boxes({"detections": []}) == []
    assert detections_to_boxes({}) == []


def test_sim_time_never_iso():
    assert stamp_to_sim_time({"sec": 44, "nanosec": 784000000}) == 44.784
    assert stamp_to_sim_time(NS(sec=48, nanosec=940000000)) == 48.94
    assert detections_sim_time(SAMPLE_ARRAY) == 44.784
    assert detections_sim_time({"detections": []}) is None
