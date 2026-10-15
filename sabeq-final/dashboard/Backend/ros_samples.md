# Sabeq — Real ROS 2 Samples (ground truth)

Real terminal outputs from the ROS machine (provided by azooz, Oct 6, 2026).
This file is the **ground truth** for nodes that are already implemented.
Where it disagrees with `ROS_TOPICS.md`, this file wins.
For topics not built yet, `ROS_TOPICS.md` is the spec.

## 0. Summary of confirmed facts

| Item | Confirmed value |
|---|---|
| Implemented node | `yolo_node` only |
| `/camera/image` | `sensor_msgs/msg/Image`, **1280×960**, encoding **`rgb8`**, `frame_id: camera_link` |
| `/person_detections` | `vision_msgs/msg/Detection2DArray`, `frame_id: camera_link` |
| `/yolo/annotated_image` | `sensor_msgs/msg/Image`, publisher `yolo_node`, QoS **RELIABLE / VOLATILE** as captured (Oct 6). **The current `yolo_node` code publishes BEST_EFFORT** (see the note in §4). |
| BoundingBox2D field path | `bbox.center.position.x` / `bbox.center.position.y` (pixels, box centre), `bbox.size_x` / `bbox.size_y` (pixels) |
| Class id | `results[0].hypothesis.class_id` = `"person"` (string, no numeric mapping needed) |
| Confidence | `results[0].hypothesis.score` (e.g. `0.8059`) |
| Detection `id` | empty string `''` (no tracking ids) |
| Timestamps | **Sim time** (`sec: 44`, `sec: 48` = seconds since sim start), not wall clock |
| `/yolo/annotated_image` rate | **~0.2–0.34 Hz** (one frame every 3–9 s) — too slow for streaming |
| Annotated image subscriber | `ros_gz_bridge` is subscribed (image is sent back into Gazebo) |
| `ros_gz_bridge` | Gazebo ↔ ROS only. `backend_bridge` is the backend's own ROS node. |

### Pending (still to collect)

| Command | Purpose | Result |
|---|---|---|
| `ros2 topic hz /camera/image` | Is Gazebo itself slow? | TODO |
| `ros2 topic hz /person_detections` | Real YOLO detection rate | TODO |
| `ros2 interface show vision_msgs/msg/BoundingBox2D` | Formal definition (field path already confirmed by the sample below) | TODO (optional) |

## 1. Conversion reference (Detection2DArray → API box)

Using the real sample below on a 1280×960 image:

| Field | Raw | Formula | API value |
|---|---|---|---|
| center x | 1003.8806 px | — | — |
| center y | 603.9335 px | — | — |
| size_x | 12.5930 px | — | — |
| size_y | 45.8818 px | — | — |
| `x` (top-left %) | | (1003.8806 − 12.5930/2) ÷ 1280 × 100 | **77.94** |
| `y` (top-left %) | | (603.9335 − 45.8818/2) ÷ 960 × 100 | **60.52** |
| `w` (%) | | 12.5930 ÷ 1280 × 100 | **0.98** |
| `h` (%) | | 45.8818 ÷ 960 × 100 | **4.78** |
| `conf` | 0.8059124946594238 | as is | **0.806** |
| `cls` | `person` | as is | **"person"** |

## 2. `/person_detections` — sample (partial, as pasted)

The paste starts partway through the first detection (its header was cut off).
The 36-value `covariance` arrays were all `0.0` in the original and are shortened here.

```yaml
# ...first detection (header cut off in the paste)...
  frame_id: camera_link
  results:
  - hypothesis:
      class_id: person
      score: 0.8059124946594238
    pose:
      pose:
        position:
          x: 0.0
          y: 0.0
          z: 0.0
        orientation:
          x: 0.0
          y: 0.0
          z: 0.0
          w: 1.0
      covariance: [0.0, ... 36 values, all 0.0]
  bbox:
    center:
      position:
        x: 1003.880615234375
        y: 603.9335327148438
      theta: 0.0
    size_x: 12.593017578125
    size_y: 45.8818359375
  id: ''
- header:
    stamp:
      sec: 44
      nanosec: 784000000
    frame_id: camera_link
  results:
  - hypothesis:
      class_id: person
      score: 0.804429292678833
    pose:
      pose:
        position:
          x: 0.0
          y: 0.0
          z: 0.0
        orientation:
          x: 0.0
          y: 0.0
          z: 0.0
          w: 1.0
      covariance: [0.0, ... 36 values, all 0.0]
  # ...rest of the message cut off in the paste...
```

Notes:
- `results[].pose` is unused (all zeros). Ignore it.
- Each detection has its own `header`, and the array also has a top-level `header`.

## 3. `/camera/image` — sample

```
$ ros2 topic echo /camera/image --once --no-arr
header:
  stamp:
    sec: 48
    nanosec: 940000000
  frame_id: camera_link
height: 960
width: 1280
encoding: rgb8
is_bigendian: 0
step: 3840
data: '<sequence type: uint8, length: 3686400>'
---
```

Notes:
- `rgb8` must be converted to BGR before OpenCV JPEG encoding, or colours come out swapped.
- 3,686,400 bytes per frame (~3.7 MB raw).

## 4. `/yolo/annotated_image` — topic info

```
$ ros2 topic info /yolo/annotated_image -v
Type: sensor_msgs/msg/Image

Publisher count: 1

Node name: yolo_node
Node namespace: /
Topic type: sensor_msgs/msg/Image
Endpoint type: PUBLISHER
GID: 01.0f.c0.cb.06.17.cb.f6.00.00.00.00.00.00.13.03.00.00.00.00.00.00.00.00
QoS profile:
  Reliability: RELIABLE
  History (Depth): UNKNOWN
  Durability: VOLATILE
  Lifespan: Infinite
  Deadline: Infinite
  Liveliness: AUTOMATIC
  Liveliness lease duration: Infinite

Subscription count: 1

Node name: ros_gz_bridge
Node namespace: /
Topic type: sensor_msgs/msg/Image
Endpoint type: SUBSCRIPTION
GID: 01.0f.c0.cb.0e.18.56.df.00.00.00.00.00.00.12.04.00.00.00.00.00.00.00.00
QoS profile:
  Reliability: RELIABLE
  History (Depth): UNKNOWN
  Durability: VOLATILE
  Lifespan: Infinite
  Deadline: Infinite
  Liveliness: AUTOMATIC
  Liveliness lease duration: Infinite
```

**Note (Oct 11, from the code, not a new capture):** the output above is from an older build. The current `ros_ws/src/sabeq_ros/sabeq_ros/yolo_node.py` publishes `/yolo/annotated_image` with its `image_qos` profile: **BEST_EFFORT**, VOLATILE, KEEP_LAST depth 1, the same profile it uses to subscribe to `/camera/image`. A RELIABLE subscriber (such as the `ros_gz_bridge` subscription shown above) does not match a BEST_EFFORT publisher, so the annotated image may no longer reach Gazebo. Re-capture `ros2 topic info /yolo/annotated_image -v` on the ROS machine to confirm.

## 5. `/yolo/annotated_image` — rate

```
$ ros2 topic hz /yolo/annotated_image
average rate: 0.339
 min: 2.912s max: 2.982s std dev: 0.03542s window: 2
average rate: 0.199
 min: 2.912s max: 9.162s std dev: 2.92970s window: 3
average rate: 0.221
 min: 2.912s max: 9.162s std dev: 2.67365s window: 4
```

Decision: because of this rate, the v1 dashboard streams **raw `/camera/image`** and draws
boxes from `/person_detections`. `/yolo/annotated_image` is not used for the stream.
