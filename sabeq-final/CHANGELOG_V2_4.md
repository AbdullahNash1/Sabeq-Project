# SABEQ V2.4 — Crowd Motion + Count Consistency

Only the two reported demo issues were changed.

## 1. Crowd clearance
- Still driven only by real `/drone/position` with `source=px4_mavsdk`.
- Trigger now uses progress along the selected route rather than raw XY radius.
- People use the existing `clear_x / clear_y` roadside targets from `world_meta.json`.
- Gazebo pose updates are limited to 4 Hz and service replies are checked.
- Prefers `/set_pose_vector/blocking`, with normal service as fallback.
- Adds `/crowd_clear/status` and the launcher verifies the node started.
- Visible movement speed is 2.5 m/s; trigger begins 12 m before the person along the route.

## 2. Dashboard crowd counts
- Route selection remains unchanged in `autonomy_node`.
- Dashboard live route counts now use `/person_detections_center`, the same topic used by `autonomy_node`.
- Dashboard live count uses the same rounded-median rule as Node 2.
- Empty `/scan/active_route` during drone rotation is no longer logged as an invalid route.
- Final values still come from Node 2 `/route_scores`, so the terminal and UI converge to the same final counts.

## Unchanged
- PX4/MAVSDK telemetry
- Drone position / altitude / heading / battery sources
- Mission flight logic
- Cart controller / speed / lead gate
- YOLO model and detection logic
- Route-selection rule
- Backend mission state machine
- `+03:07` presentation KPI from V2.3
