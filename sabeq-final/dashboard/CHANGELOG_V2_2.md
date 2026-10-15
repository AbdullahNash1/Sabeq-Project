# SABEQ Dashboard Integration V2.2

Only the requested demo-performance / consistency changes were added on top of V2.1.

## Changed

- Golf cart max speed: `8.33 m/s` (~30 km/h).
- Golf cart start delay: `1.0 s`.
- PX4 escort target lead: `30 m` ahead of the real cart odometry.
- PX4 horizontal cruise request: `12 m/s` (~43 km/h), max `15 m/s`.
- Added a cart speed governor in the adapter:
  - cart commands go to `/golf_cart/cmd_vel_raw`
  - adapter forwards them to `/golf_cart/cmd_vel`
  - cart pauses if real PX4 lead falls below 18 m
  - cart resumes at 25 m
  - required lead shrinks near the incident so the cart can finish the route
- Route ETA now matches the fast cart: `distance / 8.33 + people * 2.0`.
- Backend recommendation uses the same ETA shown on the dashboard.
- Exact ETA ties use deterministic priority: `C_east`, then `A_west`, then `B_north`.
- Arrival metric is now displayed as **time saved vs. best alternative**:
  positive = saved time, negative = slower operator override.

## Not changed

- YOLO detection logic.
- Autonomy scan / median-count logic.
- Route geometry / waypoints.
- Gazebo world generation.
- Frontend layout / design.
- Backend mission state machine.
- Rescue / return behavior (still not faked).
