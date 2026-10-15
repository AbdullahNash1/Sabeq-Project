# V2 integration notes

- Frontend still talks only to FastAPI via REST + WebSocket.
- FastAPI still uses its existing `backend_bridge` ROS integration.
- `backend_adapter_node.py` remains the compatibility / command gate.
- Before Launch the adapter publishes the docked drone pose/status.
- After Launch `mission_node.py` is the sole publisher of live `/drone/position` and `/drone/status` and the values come from MAVSDK/PX4.
- `cart_node.py` logic is unchanged except for `MAX_SPEED = 3.0`.
- Node 2 recommendation stays on `/selected_route`; execution remains gated through `/execution/selected_route`.
- No rescue / return behaviour was fabricated; the real current system still ends at the incident.
