# SABEQ V2.4.1

Fix for startup stopping at step 8 (Crowd Clearance).

- Removed the startup dependency on Gazebo Python bindings (`gz.transport13`, `gz.msgs10`).
- Crowd movement now uses Gazebo Harmonic's normal `gz service` CLI and `/world/expo_demo/set_pose_vector`.
- Crowd node still uses REAL PX4 `/drone/position` and does not change mission telemetry.
- The integrated launcher no longer aborts Backend/Frontend startup if the optional crowd visualizer is unavailable; it prints a warning and continues.
- Crowd readiness timeout increased to 20 seconds.
- Existing route selection, PX4 telemetry, cart speed, drone lead, YOLO, Backend, Frontend, and static `+03:07` KPI are unchanged.
