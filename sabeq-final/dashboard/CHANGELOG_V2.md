# SABEQ Dashboard Integration V2 — targeted changes

- Real PX4/MAVSDK drone position, heading, speed and battery are published by `mission_node`.
- Drone escort target follows the real cart odometry at a 20 m lead.
- Golf cart `MAX_SPEED` is patched to 3.0 m/s only.
- Backend startup now waits for real health before the frontend is launched.
- `routes.yaml` gets only the missing top-level `start` / `incident` values from current `world_meta.json`.
- Dashboard MJPEG is capped at 800×600, JPEG quality 68, 12 FPS. YOLO is now actually launched with the node's real `image_size=960` parameter (the old `imgsz` launch name was ignored) and the unused annotated ROS image is disabled to free CPU.
- Dashboard route ETA uses 3 m/s + 0.5 s per detected person, and the comparison label is explicit about being a time difference vs the fastest alternative.
- No rescue / return behavior was invented or changed.


## V2.1 heartbeat handoff fix
- Adapter keeps /drone/status bootstrap heartbeat during PX4 startup.
- Stops bootstrap immediately after source=px4_mavsdk is observed.
- Prevents false backend link_lost during MAVSDK connection.
