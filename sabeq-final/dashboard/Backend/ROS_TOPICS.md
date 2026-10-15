# Sabeq — ROS 2 Topics (v2.1)

This is the companion to [`API_CONTRACT.md`](./API_CONTRACT.md). It defines every ROS 2 topic between the simulation, the robot nodes and `backend_bridge`, which is the backend's own ROS node.

**What changed in v2.0.** The world is final (`docs/WORLD.md`, sources in `docs/team/`). Two of the v1.1 planned nodes now exist in the teammates' package `sabeq_ros`: Node 1 (`yolo_node`) and Node 2 (`autonomy_node`). Their real topics replace the v1.1 `route_node` spec. The rest is built in **our** package `sabeq_mission`: `mission_node` (Node 3, a port of `docs/team/mission.py`) and `world_node` (a port of the world parts of `docs/team/sim_node.py`). The v1.1 `route_node`, `px4_mission_node` and `cart_node` are gone.

**What changed in v2.1** (backend_bridge built, `Backend/app/ros/`, `app/sources/ros/`): commands wait for every acting node's `command_ack` (**3 s**, was 5 s) before the mission moves on (§4.11, §7); the backend sends `reset` on Reset sim when `SOURCE_CART=ros`; the MJPEG stream uses Pillow (§8); `system.ros` and `system.jetson` are live (§8); doc/code differences found while building it are listed in §9.

**Sources of truth**

| Status | Authority |
| --- | --- |
| **Implemented** (`yolo_node`, `autonomy_node`, Gazebo camera) | The code in `ros_ws/src/sabeq_ros/` and real outputs in [`ros_samples.md`](./ros_samples.md). Where this file disagrees with them, they win. **We never modify the teammates' files.** |
| **To build** (`mission_node`, `world_node`, `backend_bridge`) | This file is the spec |
| World facts (coordinates, speeds, rescue sequence) | [`docs/WORLD.md`](../docs/WORLD.md), collected from `docs/team/` |
| Node 3 ↔ Node 2 interface | `docs/Node3_Handoff.pdf` §4–5, §8. The PDF's world facts (route names, yaws, incident at (96, 0)) are the **old** world and do not apply. |

Platform: ROS 2 **Humble**, Python **3.10**.

## 1. Conventions

- **Message types.**
  - Camera and detections use standard ROS types (§4.1–4.3).
  - Node 2 ↔ Node 3 use plain `std_msgs`: `/scan/active_route` and `/selected_route` are `std_msgs/String` holding **only the route name**, and `/scan/complete` is `std_msgs/Bool`. `/route_scores` is a `std_msgs/String` holding Node 2's own JSON (§4.6).
  - Every other topic is `std_msgs/String` with a JSON object in `.data`, using `camelCase` keys.
- **Route ids** are the route names from `sabeq_ros/config/routes.yaml`: **`A_west`, `B_north`, `C_east`**, in scan order. Every node reads the same installed `routes.yaml`, and no node hard-codes the names or the number of routes. `tools/check_routes.py` verifies the file against `world_meta.json`.
- **Route labels** for people come from the backend's `ROUTE_LABELS` map, keyed by route id, as localised text: `A_west` → **A · West / A · غرب**, `B_north` → **B · North / B · شمال**, `C_east` → **C · East / C · شرق**. The backend refuses to start if a route in `routes.yaml` has no label. ROS messages always carry the id, never the label.
- **Time: ROS stamps are SIM time.** `header.stamp` counts seconds since the simulation started. It is never converted to an ISO date. JSON topics carry it as `simTimeS` (`sec + nanosec·1e-9`), from the publisher's ROS clock (`use_sim_time: true`). Every ISO time the backend exposes is the backend's receive or send time. Node 2's messages carry no stamp.
- **Positions** are Gazebo world ENU metres: `localM: { "e": east, "n": north }`. `mission_node` converts PX4 NED to ENU with `home_enu = (0.0, −0.1)`: `e = east + home_e`, `n = north + home_n` (inverse of `demo_common.enu_to_ned`). Only `backend_bridge` converts to lat/lon and SVG (API_CONTRACT §2).
- **Heading** `headingDeg` is a compass bearing: 0 = north, 90 = east, clockwise, in [0, 360). It equals PX4's NED yaw. From an ENU yaw: `headingDeg = (90 − yawDeg) mod 360`.
- **`missionId`** is on every mission-scoped JSON message from `sabeq_mission` and the backend. Node 2's messages have none; `backend_bridge` attributes them to the active mission. `backend_bridge` drops (and logs) JSON messages whose `missionId` is not the active mission.
- **IDs:** the drone is `SBQ-01`, the cart is `CART-01`.
- **The hub is the First Aid Center.** The cart starts the mission at the start plaza (0, 0), right beside it, and ends it at the First Aid Center stop (0, −15). Both count as "at the hub" (§4.14).

### QoS

| Topic | Publisher QoS | Subscribers use |
| --- | --- | --- |
| `/camera/image` | from `ros_gz_bridge` (verify with `ros2 topic info -v`) | **BEST_EFFORT**, VOLATILE, depth 1. `yolo_node` already does. |
| `/person_detections` | RELIABLE, VOLATILE, depth 10 (`yolo_node` code) | `autonomy_node` and `mission_node`: RELIABLE depth 10 (mission_node must not miss a frame it is waiting for). `backend_bridge`: BEST_EFFORT depth 5 (compatible). |
| `/yolo/annotated_image` | **BEST_EFFORT**, VOLATILE, depth 1 in the current `yolo_node` code (`ros_samples.md` §4 recorded RELIABLE from an older build; see the note there). | not subscribed by the backend |
| `/scan/active_route`, `/scan/complete`, `/selected_route`, `/route_scores` | RELIABLE, VOLATILE, depth 10 (`autonomy_node` code) | same |
| `/drone/position`, `/cart/pose` | BEST_EFFORT, VOLATILE, depth 5 | same |
| `/drone/status`, `/cart/status` | RELIABLE, VOLATILE, depth 10 | same |
| `/mission_events`, `/mission/command`, `/drone/payload_cmd` | RELIABLE, VOLATILE, depth 20 | same |
| `/incident` | RELIABLE, **TRANSIENT_LOCAL** (latched), depth 1 | same |

## 2. Nodes

| Node | Package | Status | Role |
| --- | --- | --- | --- |
| Gazebo + `ros_gz_bridge` | — | implemented | Simulation (`launch_sim.sh`), and the Gazebo ↔ ROS bridge only. Publishes `/camera/image`; sends `/yolo/annotated_image` back into Gazebo. |
| `yolo_node` (Node 1) | `sabeq_ros` | **implemented** | Person detection only, on `/camera/image`. Publishes `/person_detections` and `/yolo/annotated_image`. No knowledge of routes. |
| `autonomy_node` (Node 2) | `sabeq_ros` | **implemented** | Counts people per route while a route is scanned, scores the routes from `routes.yaml`, publishes `/route_scores` and `/selected_route` once at the end of the scan. |
| `mission_node` (Node 3) | `sabeq_mission` | **built, untested on PX4/ROS** (`ros_ws/src/sabeq_mission`; parameters in its README) | Port of `docs/team/mission.py`: PX4 over MAVSDK. Take-off from the cart roof, scan sequencing toward Node 2, escort ahead of the cart, watch the rescue, escort back, land on the cart. Owns the drone's speaker and LED state. Listens to `/person_detections` **only to know when fresh YOLO frames arrive** during the scan; it never counts people. |
| `world_node` | `sabeq_mission` | **built, untested on ROS/Gazebo** (`ros_ws/src/sabeq_mission`; parameters in its README) | Port of the **world** parts of `docs/team/sim_node.py` (no YOLO, no camera window): medics, cart motion, people clearing, incident circle, rescue, return to the First Aid Center, siren + EN/AR alerts. Moves Gazebo models through `set_pose_vector`. |
| `backend_bridge` | Backend | to build | The backend's ROS node. Owns the mission state machine and the 3 s auto-select countdown, relays data to the dashboard, mirrors live per-route counts, forwards operator commands. |

`world_node` replaces `sim_node.py` and `yolo_node` replaces its detector: **do not run `sim_node.py` at the same time** (both bind the UDP pose port 5603 used by `pose_sender.py`, and both would move the same models).

## 3. Topic summary

| Topic | Status | Type | Publisher | Subscribers | Rate |
| --- | --- | --- | --- | --- | --- |
| `/camera/image` | implemented | `sensor_msgs/msg/Image` | ros_gz_bridge | yolo_node, backend_bridge | TODO (`ros2 topic hz`). backend_bridge re-encodes MJPEG at ≤ 15 fps. |
| `/person_detections` | implemented | `vision_msgs/msg/Detection2DArray` | yolo_node | autonomy_node, mission_node (frame arrival only), backend_bridge | One per processed camera frame; at 1280 px about one every 3–9 s (`ros_samples.md` §5) |
| `/yolo/annotated_image` | implemented | `sensor_msgs/msg/Image` | yolo_node | ros_gz_bridge | ~0.2–0.34 Hz (measured on the older build) |
| `/scan/active_route` | implemented (sub) / to build (pub) | `std_msgs/String` | mission_node | autonomy_node, backend_bridge, world_node | Once per route, during the scan |
| `/scan/complete` | implemented (sub) / to build (pub) | `std_msgs/Bool` | mission_node | autonomy_node, backend_bridge | Once per scan |
| `/route_scores` | **implemented** | `std_msgs/String` (Node 2 JSON) | autonomy_node | backend_bridge | **Once, at scan end** |
| `/selected_route` | **implemented** | `std_msgs/String` (route name) | autonomy_node | backend_bridge, mission_node | Once, right after `/route_scores` |
| `/drone/status` | to build | `std_msgs/String` | mission_node | backend_bridge | 2 Hz (1 Hz while docked), plus on change |
| `/drone/position` | to build | `std_msgs/String` | mission_node | backend_bridge, world_node | 10 Hz |
| `/mission_events` | to build | `std_msgs/String` | mission_node, world_node | backend_bridge | On event |
| `/mission/command` | to build | `std_msgs/String` | backend_bridge (mission_node only in standalone mode, §5.3) | mission_node, world_node | On command |
| `/drone/payload_cmd` | to build | `std_msgs/String` | backend_bridge | mission_node, world_node | On operator toggle |
| `/cart/pose` | to build | `std_msgs/String` | world_node | backend_bridge, mission_node | 10 Hz |
| `/cart/status` | to build | `std_msgs/String` | world_node | backend_bridge, mission_node | **10 Hz** (mission_node escorts from it) |
| `/incident` | to build | `std_msgs/String` | backend_bridge | — (no subscriber in v2.0) | On activation and on clear (latched) |

## 4. Message schemas

### 4.1 `/camera/image` — `sensor_msgs/msg/Image` (implemented)

Confirmed in `ros_samples.md` §3: **1280 × 960**, encoding **`rgb8`**, `step` 3840, `header.frame_id` `camera_link`, `header.stamp` sim time.

How `backend_bridge` uses it:
- **MJPEG stream** (`GET /api/drones/SBQ-01/camera/stream`): Pillow `Image.frombuffer` in the frame's own `encoding` (`rgb8`; `bgr8` and `mono8` are handled too), so no channel swap is needed (that was a cv2 detail). Each frame is JPEG-encoded **once**, in a worker thread, and shared by every stream client; each client gets ≤ 15 fps. Nothing is encoded while no client is connected.
- **Image size cache:** keep `width` and `height` from the latest message for §4.2 and `CameraInfo.resolution`.
- **Heartbeat:** when the drone part is not ROS (`SOURCE_DRONE=mock`), camera frames are the ROS heartbeat: no frame for 5 s → `system.ros.ok = false`. With `SOURCE_DRONE=ros`, `/drone/status` is the heartbeat (§4.8).

### 4.2 `/person_detections` — `vision_msgs/msg/Detection2DArray` (implemented)

Published by `yolo_node` for every processed frame, with the camera frame's `header`. Each `Detection2D` has `bbox.center.position.{x,y}` (pixels, box centre), `bbox.size_x/size_y` (pixels), and one `results[0].hypothesis` with `class_id = "person"` and `score`. `id` is empty and `results[].pose` is zero: ignore both. (`yolo_node.py`, `ros_samples.md` §2)

**Conversion to an API box** (`ros_samples.md` §1). `W` and `H` are from the latest `/camera/image`, fallback 1280 × 960:

| API field | Formula |
| --- | --- |
| `x` | `(center.x − size_x / 2) / W × 100` |
| `y` | `(center.y − size_y / 2) / H × 100` |
| `w` | `size_x / W × 100` |
| `h` | `size_y / H × 100` |
| `conf` | `results[0].hypothesis.score`, 3 decimals |
| `cls` | `results[0].hypothesis.class_id` (`"person"`) |

`mission_node` uses only `header.stamp` and the arrival of each message, to pace the scan (§4.4). It ignores the detections themselves.

`x`/`y` are the top-left corner. All four are percentages, 2 decimals, clamped to [0, 100]. Detections with empty `results` are skipped. `simTimeS` comes from the array `header.stamp`; `frameStamp` is the backend's receive time. Boxes carry no route. Worked example: `ros_samples.md` §1 → x 77.94, y 60.52, w 0.98, h 4.78, conf 0.806.

`yolo_node` counts **the whole frame**. Node 2 uses `len(detections)` of the whole frame as the route count, while `mission.py` counted only the centre ±15° band. With routes 45° apart and a ~100° camera, neighbouring routes can leak into a count. This is Node 2's behaviour and we do not change it; the backend's live mirror (§6.1) uses the same rule so the numbers match.

### 4.3 `/yolo/annotated_image` — `sensor_msgs/msg/Image` (implemented, not used by the backend)

Camera frames with the boxes drawn. Too slow to stream (`ros_samples.md` §5). The dashboard streams `/camera/image` and draws the boxes from `/person_detections` itself.

### 4.4 `/scan/active_route` — `std_msgs/String` (mission_node → Node 2)

`data` is the route name, e.g. `"A_west"`. As in `Node3_Handoff.pdf` §5:
- `mission_node` publishes it **after** the drone's yaw is within 8° of the route's scan yaw and has settled 0.15 s (`mission.py` `SCAN_YAW_TOL`, `SCAN_SETTLE`), never before.
- One message per route, in `routes.yaml` `scan_order`: A_west, B_north, C_east.
- The first message of a mission makes Node 2 reset its counts and start a new scan.
- The scan yaw of each route comes from `world_meta.json` `routes.<name>.scan_yaw_ned_deg` (315.0°, 0.0°, 45.0°).
- **Per scan heading**, `mission_node` does this, in order:
  1. Yaw to the route's scan yaw and wait until it is within the yaw tolerance (`scan_yaw_tol_deg`, default 8.0, `mission.py` `SCAN_YAW_TOL`).
  2. Settle (`scan_settle_s`, default 0.15, `mission.py` `SCAN_SETTLE`). The end of the settle is `t_settle`, read from the node's ROS clock.
  3. Publish the route name on `/scan/active_route`.
  4. Hold the heading until **both** are true: at least `scan_min_frames` `/person_detections` messages have arrived whose `header.stamp` is **after `t_settle`**, and at least `scan_min_hold_s` has passed since the publish. Stop holding at `scan_max_hold_s` in any case.
- Parameters (they replace v2.0's fixed `scan_hold_s`):

  | Parameter | Default | Meaning |
  | --- | --- | --- |
  | `scan_min_frames` | 2 | Fresh detection messages (stamp after `t_settle`) to wait for. Any message counts, including one with zero detections. |
  | `scan_min_hold_s` | 1.0 | Shortest hold after the publish |
  | `scan_max_hold_s` | 10.0 | Longest hold. If it is reached with fewer than `scan_min_frames` fresh frames, log a warning (as `mission.py` does: "no fresh detections") and go on to the next route. |

- **Why not a fixed hold:** Node 1 runs YOLO at 1280 px and was measured at one frame every 3–9 s (`ros_samples.md` §5). A fixed 2 s hold could give Node 2 zero frames for a route. `mission.py` waited for fresh frames in the same way (`SCAN_MIN_FRAMES`), but read them from `sim_node.py`'s UDP status.
- **Same clock:** `mission_node` runs with `use_sim_time: true`, so `t_settle` and the camera's `header.stamp` (both Gazebo sim time) are comparable. Frames taken while the drone was still turning have a stamp before `t_settle` and do not count.
- Node 2 counts by **arrival**, not by stamp. A frame taken during the turn that arrives after the publish is still counted by Node 2 for the new route. Waiting for fresh frames makes sure the route gets real frames; it cannot remove that one stale frame.

`world_node` also subscribes: on the **first** `/scan/active_route` after a `launch`, it sends the medics running from the First Aid Center to the cart (`sim_node.py` `Rescue.dispatch`).

### 4.5 `/scan/complete` — `std_msgs/Bool` (mission_node → Node 2)

`data: true`, published **once** after the last route's scan window. Node 2 then publishes `/route_scores` and `/selected_route`. Node 2 ignores `false`.

### 4.6 `/route_scores` — `std_msgs/String` (implemented, Node 2)

Published **once per scan**, when `/scan/complete` arrives, right before `/selected_route`. `data` is JSON keyed by route name, in `routes.yaml` order:
```json
{
  "A_west":  { "distanceM": 151.2, "peopleCount": 18, "estimatedTimeS": 30.6 },
  "B_north": { "distanceM": 112.0, "peopleCount": 26, "estimatedTimeS": 29.0 },
  "C_east":  { "distanceM": 151.2, "peopleCount": 10, "estimatedTimeS": 26.6 }
}
```
- `distanceM` is the polyline length of the route's `routes.yaml` waypoints (cart start → route → incident).
- `peopleCount` is the **highest** `len(/person_detections.detections)` seen while that route was active.
- `estimatedTimeS = distanceM / 7.0 + peopleCount × 0.5`. Node 2 owns the formula; the backend never recomputes it.
- All values are rounded to 2 decimals. There is no `missionId`, `simTimeS` or `final` flag.
- The example uses the scan-zone people of the final world (18 / 26 / 10). Real values depend on YOLO.

### 4.7 `/selected_route` — `std_msgs/String` (implemented, Node 2)

`data` is the route name with the lowest `estimatedTimeS`, e.g. `"C_east"`. Published once, right after `/route_scores`. This is a **recommendation**: the route actually driven is the one in `/mission/command` `select_route` (§4.11), which may be an operator override.

### 4.8 `/drone/status` (mission_node)
```json
{
  "droneId": "SBQ-01",
  "simTimeS": 54.5,
  "missionId": "MSN-0418",
  "state": "scanning",
  "armed": true,
  "dockedOnCart": false,
  "batteryPct": 97,
  "flightTimeLeftS": 1480,
  "link": "ok",
  "px4Mode": "OFFBOARD",
  "payload": { "camera": true, "speaker": true, "led": true },
  "payloadState": { "speaker": true, "led": true },
  "cameraMode": "scan_360",
  "scan": { "phase": "rotate", "progress": 0.67, "route": "B_north", "coveragePct": 67 },
  "fault": null
}
```
- `state`: `docked | taking_off | scanning | escorting | returning_to_dock | landing | fault | offline`. `escorting` covers both escort legs **and** the hover over the rescue. `returning_to_dock` is only the abort flight back to the cart.
- `px4Mode` is the PX4 flight mode name from MAVSDK (diagnostics only).
- `payloadState` is the **enabled** state of the speaker and LED, confirming `/drone/payload_cmd`. Whether the siren is actually sounding also depends on the mission phase (§5.2).
- `cameraMode`: `off | scan_360 | forward`. `scan_360` is kept as the enum value for the three-heading scan.
- `scan` is `null` outside `taking_off` and `scanning`. `phase`: `climb | rotate | done`. In `rotate`, `progress = routes finished / routes in scan_order`, `route` is the route being scanned (or `null` while turning), and `coveragePct = progress × 100`.
- `fault` is `null` or `{ "code", "message" }`; non-null forces `state: "fault"`. Codes: `link_lost` (mavsdk_server stream died), `disarmed`, `left_offboard` (checked from the scan onwards, as in `mission.py`; on the way to the scan point only `armed` is checked), `arm_refused`, `px4_not_ready` (the `Aborted` / `SystemExit` cases in `mission.py`), `offboard_failed` (`offboard.start()` refused), `offboard_not_engaged` (PX4 accepted `offboard.start()` but its `flight_mode` never reported OFFBOARD within 5 s), `land_failed`, `cart_status_lost` (no `/cart/status` during a leg; the drone holds position).
- A drone hovering with no mission (after Ctrl+C, or `mission_node` started while the drone flies) reports `escorting` with `missionId: null`.
- **Heartbeat:** no message for 5 s → `system.ros.ok = false` and an active mission goes to `error` with `link_lost`.

### 4.9 `/drone/position` (mission_node)
```json
{
  "droneId": "SBQ-01",
  "simTimeS": 54.52,
  "localM": { "e": 0.0, "n": 0.0 },
  "altitudeM": 8.1,
  "headingDeg": 0.0,
  "speedMps": 0.3
}
```
- `localM` is world ENU (§1), from PX4 `position_velocity_ned` + `home_enu`.
- `altitudeM` is PX4's relative altitude, i.e. height above the cart roof (≈ 8 while scanning and escorting).
- `world_node` uses it in place of `sim_node.py`'s Gazebo pose subscription: for the cart's lead factor, for the people clearing, and for the drone marker.

### 4.10 `/mission_events`
```json
{
  "missionId": "MSN-0418",
  "simTimeS": 50.0,
  "source": "mission_node",
  "event": "scan_altitude_reached",
  "details": {}
}
```

| `event` | `source` | `details` | Meaning |
| --- | --- | --- | --- |
| `command_ack` | mission_node, world_node | `{ "commandId", "accepted": true, "reason": null }` | Reply to every `/mission/command` the node acts on |
| `takeoff_started` | mission_node | `{}` | Armed, leaving the cart roof |
| `scan_altitude_reached` | mission_node | `{ "altitudeM": 8 }` | At the scan point (0, 0) at 8 m. The scan starts. |
| `scan_rotation_complete` | mission_node | `{}` | Last route scanned, right before `/scan/complete` |
| `escort_started` | mission_node | `{ "leg": "outbound" \| "return", "route": "C_east" }` | The drone is in position ahead of the cart |
| `rescue_watch_started` | mission_node | `{}` | Cart arrived; the drone hovers 8 m behind it |
| `patient_loaded` | world_node | `{}` | Medics are back on the cart with the patient (`Rescue` state `READY`). The return may start. |
| `patient_delivered` | world_node | `{}` | Medics carried the patient into the First Aid Center (`INSIDE`) |
| `landing_started` | mission_node | `{}` | Descent onto the cart roof |
| `landed_on_dock` | mission_node | `{}` | On the cart roof, disarmed, PX4 in HOLD. **Ends a returning mission.** |
| `battery_low` | mission_node | `{ "batteryPct": 18 }` | Below the reserve threshold (once per mission) |
| `cart_fallback` | mission_node | `{ "leg", "route", "speedMps": 10.0 }` | No `/cart/status` at all when a leg starts: with `no_cart_fallback` the drone flies the path alone at `speedMps` (testing without world_node) |
| `node_error` | mission_node, world_node | `{ "code", "message" }` | Unrecoverable node failure |

`node_error` codes from mission_node:
- **`selected_route_timeout`:** no `/selected_route` within 30 s after the scan. The drone lands on the cart.
- **`select_route_timeout`:** a recommendation arrived, but no `select_route` came. The drone lands on the cart.
- **`cart_status_timeout`:** `/cart/status` is silent for 3 s during a leg. The drone holds (`fault: cart_status_lost`).
- **`rescue_timeout`:** with `auto_return` only, the patient is not loaded within 120 s. The drone lands on the cart.
- **`px4_not_connected`:** MAVSDK found no PX4 at startup.
- **Fault codes:** each `/drone/status` fault (§4.8) is also emitted as a `node_error` with the same code.

`node_error` codes from world_node:
- **`pose_sender_missing`:** `docs/team/pose_sender.py` not found at `pose_sender_path`.
- **`pose_sender_died`:** it exited (usually: Gazebo is not running).

In both cases world_node keeps publishing `/cart/*`; only the Gazebo models stop moving.

### 4.11 `/mission/command` (backend_bridge → mission_node, world_node)
```json
{
  "commandId": "6f1c2a0e-6b0d-4d55-9a57-0c2d9b1f4e11",
  "missionId": "MSN-0418",
  "simTimeS": 61.0,
  "command": "select_route",
  "route": "C_east",
  "source": "auto",
  "polylineLocalM": [[0, 0], [36, 36], [36, 92], [16, 112], [0, 112]],
  "scanAltitudeM": null,
  "reason": null
}
```

| `command` | Set fields | mission_node | world_node |
| --- | --- | --- | --- |
| `launch` | `scanAltitudeM: 8` | Take off from the cart roof, fly to the scan point, scan (§4.4–4.5), wait for `select_route` | Once the drone is airborne (`/drone/position` `altitudeM ≥ 2`), **reset the world** (cart, crowd, medics, patient back to their start poses), as `mission.py` does after take-off |
| `select_route` | `route`, `source` (`auto` \| `operator`), `polylineLocalM` (cart path from `routes.yaml`) | Siren/LED on, move 15 m ahead, escort the cart along the route | Start the cart on the route at 10 m/s with the 15 m lead (`sim_node.py` `cart_start`); the medics jump aboard |
| `start_return` | `route`, `polylineLocalM` (`demo_common.build_return_path`: incident → route reversed → start → First Aid Center) | Escort the cart back, 15 m ahead. Accepted whenever the drone is watching the rescue: mission_node does **not** gate on `patient_loaded` (its copy of `/cart/status` can lag world_node's event; world_node owns the check) | **Only after `patient_loaded`:** turn the cart (1.2 s) and drive the return path to the First Aid Center. Rejected (`accepted: false`, `reason: "patient_not_loaded"`) before that. |
| `abort` | `reason` (`operator_abort` \| `return_to_dock` \| `low_battery`) | Fly to the cart and land on its roof (`land_on_cart`) | **Stop the cart in place**, siren off. The operator then drives back manually. |
| `reset` | — | **Ignored** (no ack): not a drone command | Put the cart, crowd, medics and patient back where `gen_world.py` placed them, at once (`sim_node.py` reset). The backend sends it (`missionId: null`) on Reset sim (`POST /api/dev/sim/reset`) when `SOURCE_CART=ros`, without waiting for the ack. |

- Both nodes take their geometry from `routes.yaml` / `world_meta.json`; `polylineLocalM` is for logging and cross-checks only, so the route geometry has one source of truth (`Node3_Handoff.pdf` §11).
- Each node that acts on a command replies with `command_ack` on `/mission_events` with the same `commandId`. Both nodes ack `launch`, `select_route`, `start_return` and `abort`; only world_node acks `reset`. A node ignores (and never acks) a command it does not act on.
- world_node rejection reasons:
  - **`select_route`:** `unknown_route`, or `invalid_state` (the cart is already driving).
  - **`start_return`:** `patient_not_loaded`, or `no_rescue_models` (a world without medics).
  - **Any command:** `unknown_mission`.

  A `start_return` while the cart is already on the way back is accepted (idempotent).
- **The backend waits for the acks before it moves the mission on.** It publishes each command **once** (whichever ROS parts are active), then waits up to **3 s** (`command_ack_timeout_s`) for an ack from every acting node of an active ROS part: mission_node when `SOURCE_DRONE=ros`, world_node when `SOURCE_CART=ros`.
  - **Operator commands** (REST `launch`, `route`, `return`, `abort`): all accepted → the transition happens and the REST call returns. `accepted: false` → **409 `command_rejected`** (`details: {command, node, reason}`), no ack → **504 `ros_timeout`** (`details: {command, nodes, timeoutS}`); the mission stays in its state and the dashboard shows a toast.
  - **Commands the backend sends by itself** (the auto-select countdown's `select_route`, the low-battery `abort`): a rejection or a missing ack moves the mission to `error` (`command_rejected` / `command_timeout`).
- Unused fields are `null`. `simTimeS` comes from backend_bridge's ROS clock.

### 4.12 `/drone/payload_cmd` (backend_bridge → mission_node, world_node)
```json
{ "missionId": "MSN-0418", "simTimeS": 78.0, "speaker": false, "led": null }
```
- `null` means "leave unchanged". Both start **enabled** at every `launch`.
- `mission_node` confirms the change in `/drone/status` `payloadState`. `world_node` applies `speaker` to the siren + EN/AR alert loop (§5.2).

### 4.13 `/cart/pose` (world_node)
```json
{
  "cartId": "CART-01",
  "simTimeS": 78.1,
  "source": "gazebo",
  "localM": { "e": 36.0, "n": 52.4 },
  "headingDeg": 0.0,
  "speedKmh": 36.0
}
```
- The pose `world_node` itself commands to Gazebo (`world_node` is the only thing moving the cart). `headingDeg` from the cart's ENU yaw (§1); `speedKmh = cart_v × 3.6`.

### 4.14 `/cart/status` (world_node)
```json
{
  "cartId": "CART-01",
  "simTimeS": 96.0,
  "missionId": "MSN-0418",
  "state": "at_incident",
  "phase": "ARRIVED AT INCIDENT",
  "route": "C_east",
  "leg": "outbound",
  "pathS": 146.2,
  "pathLenM": 151.2,
  "speedMps": 0.0,
  "rescue": { "state": "TREAT", "progress": "in_progress" },
  "retPhase": null,
  "distanceToTargetM": 5.0
}
```

| Field | Values | From `sim_node.py` |
| --- | --- | --- |
| `state` | `at_hub` (before the start, or `retPhase = DONE`) \| `to_incident` (outbound moving) \| `at_incident` (`cart_done`, until the turn) \| `to_hub` (`TURN`, `RETURN`) \| `stopped` (after `abort`) | derived |
| `phase` | the human-readable phase string (`WAITING FOR MISSION`, `SCANNING A_west`, `ESCORTING ON C_east`, `ARRIVED AT INCIDENT`, `MEDICS TREATING PATIENT`, `LOADING PATIENT ON STRETCHER`, `PATIENT ON BOARD`, `RETURNING TO FIRST AID CENTER`, `PATIENT DELIVERED TO FIRST AID`, and `STOPPED` after `abort`) | `S.phase` |
| `leg` | `outbound` \| `return` \| `null` | — |
| `pathS`, `pathLenM` | progress and length along the current leg's path (m) | `cart_s` / `path_len` outbound, `ret_s` / `ret_len` on return |
| `speedMps` | current cart speed | `cart_v` |
| `rescue.state` | `AT_BASE`, `MOVING`, `ON_CART`, `TREAT`, `LOAD`, `READY`, `RIDING_BACK`, `INSIDE` (raw `Rescue.state`), or `null` in a world without rescue models | `rescue` |
| `rescue.progress` | `none` (before arrival) \| `in_progress` (arrived, until `READY`) \| `patient_loaded` (`READY` and later) \| `delivered` (`INSIDE`) | derived |
| `retPhase` | `null` \| `TURN` \| `RETURN` \| `DONE` | `ret_phase` |
| `distanceToTargetM` | remaining distance along the leg to its stop point, or `null` with no target | derived |

`mission_node` reads `/cart/status` and `/cart/pose` exactly where `mission.py` read the UDP status: `pathS` and `speedMps` for the lead, `state: at_incident` to end the outbound escort, `rescue.progress` for the rescue watch, `retPhase: DONE` to end the return escort, and the cart pose to land on the roof. `/cart/status` comes at 10 Hz (`mission.py` had 15 Hz UDP). mission_node still extrapolates `pathS` with `speedMps` since the message arrived, so the drone doesn't trail by the message age and the lead factor doesn't slow the cart.

### 4.15 `/incident` (backend_bridge, latched)
```json
{
  "active": true,
  "missionId": "MSN-0418",
  "incidentId": "INC-0419",
  "simTimeS": 40.0,
  "severity": "critical",
  "type": "cardiac_arrest",
  "zone": "B",
  "localM": { "e": 0.0, "n": 112.0 },
  "scanAltitudeM": 8
}
```
- Published when an incident becomes active. On close or dismiss: `{ "active": false, "missionId": null, "simTimeS": ... }`.
- The world has **one fixed incident** at (0, 112), so no `sabeq_mission` node subscribes to this topic. It is kept for logging and for future nodes. `mission_node` and `world_node` take `missionId` from the `launch` command (§4.11) and tag all their output with it until the next `launch`.

## 5. Decisions

### 5.1 Who decides what

1. **The route is locked in by the backend.** `mission_node` does not act on `/selected_route` by default. It waits for `/mission/command` `select_route`, which the backend sends when the 3 s auto-select countdown expires (route = Node 2's pick, `source: auto`) or when the operator overrides (`source: operator`). Parameter **`auto_execute_selected_route`** (default `false`): when `true`, `mission_node` acts on `/selected_route` directly, as in `Node3_Handoff.pdf` §5 step 8 (standalone tests without the backend).
2. **The return starts on the backend's `start_return`.** The UI enables **Start return** only after `world_node` reports the patient loaded (`/mission_events` `patient_loaded`, also `/cart/status` `rescue.progress`). `world_node` holds the cart at the incident with the patient aboard until then, instead of turning back automatically as `sim_node.py` does. Parameter **`auto_return`** on `mission_node` (default `false`): when `true`, `mission_node` sends `start_return` itself on `patient_loaded`.
3. **The backend mirrors live per-route counts for display** (§6.1), because Node 2 publishes only once, at the end of the scan. Node 2's `/route_scores` replaces the mirror when it arrives and is the only input to the decision.
4. **Route labels** shown to people are localised ("C · East" / "C · شرق"), from the backend's `ROUTE_LABELS` map (§1).
5. **The "hub" is the First Aid Center** (§1). The mission ends there: the cart stops at (0, −15), the medics carry the patient inside, and the drone lands on the cart.

### 5.2 Siren and alerts

`world_node` plays the siren + English + Arabic alert loop from `sim_node.py` (`Siren`). It is audible when **both** are true:
- the cart is on a leg: outbound (`to_incident`) or return (`TURN`, `RETURN`), the same schedule as `mission.py` (on at escort start, off at arrival, on for the return, off at the First Aid Center), and
- the speaker is enabled (`/drone/payload_cmd` `speaker`, default on).

### 5.3 Standalone mode

With `auto_execute_selected_route` or `auto_return` set, `mission_node` publishes the matching `select_route` / `start_return` on `/mission/command` itself (`source: "auto"`, a fresh `commandId`), so `world_node` keeps a single input topic. mission_node handles its own command at once and ignores its echo (same `commandId`), so each command is acknowledged once. Without world_node (`no_cart_fallback`, default on), the legs are flown against a virtual cart at `cart_fallback_speed_mps` (event `cart_fallback`). Without the backend, a launch is triggered with `ros2 topic pub --once /mission/command std_msgs/String '{data: "{\"command\":\"launch\",\"missionId\":\"TEST\",\"commandId\":\"t1\",\"scanAltitudeM\":8}"}'`.

## 6. Backend behaviour

### 6.1 Live counts mirror

During `scanning`, `backend_bridge` subscribes to `/scan/active_route` and `/person_detections` and applies **Node 2's rule**: per route, keep the highest `len(detections)` seen while that route is active, reset on the first `/scan/active_route` of a mission. It relays these as `route.scores` (`final: false`). Route length comes from the same `routes.yaml`; `estTimeS` stays `null` until Node 2's `/route_scores` arrives.

### 6.2 Mapping `/route_scores` to the API `Route`

| API `Route` field | From |
| --- | --- |
| `id` | the JSON key (`A_west`) |
| `lengthM` | `distanceM` |
| `peopleCount` | `peopleCount` |
| `estTimeS` | `estimatedTimeS` |
| `countFinal` | `true` once `/route_scores` has arrived |
| `label` | `ROUTE_LABELS[id]`, localised text `{en, ar}` (§1) |
| `polyline` | the route's `routes.yaml` waypoints, projected (API_CONTRACT §2) |
| `score` | derived by the backend: the fastest `estTimeS` / this route's `estTimeS` (fastest = 1.0), `null` while `estTimeS` is `null` |
| `crowdFactor` | `null` (Node 2 has none) |

## 7. Which ROS message triggers which mission transition

| Transition | Trigger | Who |
| --- | --- | --- |
| `idle → incident_received` | REST `POST /incidents` (or queue pop) → **publishes** `/incident` | backend |
| `incident_received → idle` | REST `POST dismiss` → **publishes** `/incident` `{active:false}` | backend |
| `incident_received → taking_off` | REST `POST launch` → **publishes** `/mission/command` `launch` | backend |
| `taking_off → scanning` | `/mission_events` `scan_altitude_reached` | mission_node |
| `scanning → route_selected` | `/selected_route` (after `/route_scores`) | autonomy_node |
| `route_selected → escorting` | The 3 s countdown expires, or REST `POST route` → **publishes** `/mission/command` `select_route` | backend |
| `escorting → arrived` | `/cart/status` `state: "at_incident"` | world_node |
| *(within `arrived`)* rescue `in_progress → patient_loaded` | `/mission_events` `patient_loaded` (or `/cart/status` `rescue.progress`) | world_node |
| `arrived → returning` | REST `POST return` (allowed only after `patient_loaded`) → **publishes** `/mission/command` `start_return` | backend |
| *(within `returning`)* cart at the First Aid Center | `/cart/status` `state: "at_hub"` | world_node |
| `returning → completed` | `/mission_events` `landed_on_dock` | mission_node |
| `* → aborted` (operator) | REST `POST abort` → **publishes** `/mission/command` `abort` | backend |
| `* → aborted` (`low_battery`) | `/mission_events` `battery_low` → backend **publishes** `/mission/command` `abort` `{reason:"low_battery"}` | mission_node |
| `* → error` | `/drone/status` `state: "fault"` / non-null `fault` / `link: "lost"`; no `/drone/status` for 5 s; `/mission_events` `node_error`; for the backend's own commands (countdown, low battery), a `command_ack` with `accepted: false` or none within 3 s | any |
| `completed / aborted / error → idle` | REST `POST close` → **publishes** `/incident` `{active:false}` | backend |

Command transitions happen once every acting node has acknowledged the command (§4.11), not when it is published. Camera frames and detections never trigger transitions.

## 8. Topic → dashboard (WS) relay

| ROS topic | WS message(s) | Notes |
| --- | --- | --- |
| `/camera/image` | — (HTTP MJPEG), `system.status` | `GET /api/drones/SBQ-01/camera/stream`. Sets `CameraInfo.resolution`. |
| `/person_detections` | `camera.detections`, `system.status` | Converted per §4.2, ≤ 10 Hz. Their rate over the last 5 s is `system.jetson.fps` (`ok` while > 0). `counts` from the live mirror (§6.1), then from `/route_scores`. |
| `/scan/active_route` | `scan.progress`, `route.scores` | Current route for the scan view; drives the live mirror |
| `/route_scores` | `route.scores` (`final: true`) | Mapped per §6.2 |
| `/selected_route` | `route.recommendation`, `mission.state` | Starts the 3 s countdown |
| `/drone/status` | `drone.telemetry`, `scan.progress`, `payload.state`, `camera.info`, `system.status` | |
| `/drone/position` | `drone.telemetry`, `scan.progress`, `escort.progress` | ≤ 5 Hz |
| `/mission_events` | `mission.state` (transitions and rescue progress), `error` | |
| `/cart/pose` | `cart.telemetry`, `escort.progress` | ≤ 5 Hz |
| `/cart/status` | `cart.telemetry`, `escort.progress`, `mission.state` (transitions, rescue progress) | `pathS / pathLenM` is the escort progress |

## 9. Doc / code differences

Found while building `backend_bridge` against the nodes' code (`sabeq_ros` read-only, `sabeq_mission` ours). Where the code is a teammate's, the backend follows the code.

1. **Node 2 counts during turns** (`autonomy_node.py`). Node 2 keeps counting `/person_detections` for the active route until the **next** `/scan/active_route` arrives, and mission_node publishes the next route only after the drone has turned and settled (§4.4). Frames taken during the turn toward the next heading therefore count toward the **previous** route. **Effect: it can inflate the previous route's count, never the next one's** (a route only becomes active once the drone faces it, and its count is reset with the mission's first route). Seen in the co-simulation: A_west (18 people in its scan zone) is sometimes reported as 26, the count of B_north, the next heading; C_east, the last route, is never affected. This can only make a route look slower, never faster than its own scan zone. The backend's live mirror (§6.1) applies the same rule, so live and final counts agree. A fix (ignore frames until the drone has settled, or stamp frames with the route) would be in the teammates' code.
2. **Ack timeout is 3 s, not 5 s**, and commands move the mission on only after the acks (§4.11, §7). The earlier text moved the mission at once and fell back to `error` afterwards.
3. **`reset` is sent by the backend** on Reset sim when `SOURCE_CART=ros` (§4.11). Earlier text said the backend never sends it. Only world_node acks it; mission_node ignores it, as before.
4. **`system.ros` and `system.jetson` were never updated** with mock sources (fixed values). With ROS sources they are live: `ros` from the `/drone/status` heartbeat (or camera frames without a ROS drone), `jetson.fps` from the detection rate.
5. **mission_node reports `escorting` with `missionId: null`** when it is airborne without a mission (for example a manual take-off in standalone tests). The backend never acts on a status that carries another mission's id. A `null`-tagged status still updates the drone's telemetry and payload confirmation, but scan progress and faults are taken only from a status tagged with the active mission's id.
6. **`/incident` has no subscriber** among the nodes (§4.15). The backend still publishes it, latched, once per change.
7. **QoS:** `/person_detections` is RELIABLE depth 10 in `yolo_node`; the backend reads it BEST_EFFORT depth 5 (compatible). `/yolo/annotated_image` is BEST_EFFORT in the current `yolo_node` code although `ros_samples.md` §4 recorded RELIABLE (§1 QoS). The backend does not subscribe to it.
8. **MJPEG:** §8 used to say RGB → BGR and `cv2.imencode`. The backend uses Pillow (no OpenCV dependency), which reads `rgb8` directly.
