# SABEQ V2.5 — Judge Demo Polish

- Route decision logic is unchanged: CENTER ROI + median, least people first, shortest distance only as a tie-breaker.
- Removed the “Pedestrians warned / مشاة تم تنبيههم” summary KPI from the frontend.
- Kept the static presentation KPI `+03:07` for time saved.
- Camera path optimized for the live dashboard: MJPEG 800x600, JPEG 68, 12 FPS.
- Fixed the YOLO launch parameter from the unused `imgsz` name to the node's real `image_size=960`.
- Disabled the unused `/yolo/annotated_image` generation at runtime to reduce CPU load; detections remain unchanged and the dashboard still overlays boxes.
- Added Gazebo rescue animation: two medics dismount, carry the stretcher to the patient, load the patient, and carry the patient back onto the cart.
- After loading, the system automatically returns on the same selected route in reverse, then continues to the First Aid Center.
- The real PX4 mission node escorts the returning cart and continues publishing real position, altitude, heading, speed, and battery.
- Return leg uses the same real-PX4 lead governor so the golf cart cannot overtake the drone.
