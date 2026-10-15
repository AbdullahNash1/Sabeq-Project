# SABEQ V2.6 — Judge Demo Polish

Only the three requested areas were changed.

## 1) Smoother live dashboard camera
- Stream stays LIVE from `/camera/image`; no prerecorded/fake video.
- Dashboard JPEG output: 640×480, quality 62, max 10 FPS.
- YOLO inference reduced from 960 to 640.
- Added a 0.15 s inference cooldown after each prediction to free CPU/GPU time for Gazebo, PX4, FastAPI and MJPEG.
- `/yolo/annotated_image` stays disabled; the frontend still overlays detections itself.

## 2) Continuous PX4 escort
- The old `cart position + 30 m` fixed setpoint naturally caused stop/wait/move cycles.
- The drone now follows a continuously advancing route cursor at 10.5 m/s, while the target is never allowed behind `cart + 30 m`.
- The same continuous logic is used on automatic return.
- Real MAVSDK/PX4 telemetry is unchanged.

## 3) No manual return button
- Removed the Arrived-state `Start return` button.
- Existing rescue logic still publishes `/rescue/return_start=True` automatically once the patient is secured on the cart.

Unchanged: route selection, golf-cart speed, crowd-clearance behaviour, rescue animation sequence, PX4 telemetry schema, and the static +03:07 KPI.
