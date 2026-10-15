# SABEQ V2.7 — Stable Route Escort + 1280 YOLO

Only the requested regression fixes are included.

## 1. Drone route movement restored
- Reverted the V2.6 continuously advancing moving-target escort.
- Restored the stable V2.5 escort logic: the PX4 setpoint is calculated only from the **selected route** and the real golf-cart progress, approximately 30 m ahead.
- This prevents PX4 from chasing a target that advances around corners faster than the aircraft and cutting diagonally across the site / neighbouring routes.
- The same selected-route logic is used on the automatic return.
- Real PX4/MAVSDK position, altitude, heading, speed and battery are unchanged.

## 2. YOLO restored to 1280
- Runtime `image_size` restored to `1280`.
- Removed V2.6 inference cooldown.
- Annotated ROS image remains disabled because the dashboard does not need it.

## 3. Camera optimisation retained
- The dashboard MJPEG path remains the lighter V2.6 configuration: max 640x480, JPEG 62, max 10 FPS.
- This optimisation affects only the video sent to the dashboard; YOLO still receives the original ROS camera image and performs inference at 1280.

## 4. Existing requested behaviour retained
- No manual return button.
- Rescue / patient-loading / automatic return retained.
- Crowd clearance retained.
- Static `+03:07` saved-time KPI retained.
