"""ROS JSON messages -> MissionService hook arguments (pure functions, no ROS import).

Formats are those the real nodes publish (read from their code, ROS_TOPICS.md v2.0):
- /route_scores: autonomy_node (Node 2): {route: {distanceM, peopleCount, estimatedTimeS}}
- /drone/status, /drone/position: sabeq_mission mission_node (messages.py)
- /cart/status, /cart/pose: sabeq_mission world_node (world_logic.py)
World ENU metres are projected to map x/y with the §2 projection (service.local_position).
"""
from app.models import Route


# Keep dashboard ETA aligned with the real cart controller used in this integration.
ROUTE_CART_SPEED_MPS = 8.33
CROWD_DELAY_S_PER_PERSON = 2.0


def _label(route):
    return dict(route.label)


def live_routes(world, counts: dict, local_position) -> list:
    """Route list during the scan: live max counts, no estimate yet (ROS_TOPICS §6.1)."""
    return [
        Route(id=rid, label=_label(r), length_m=round(r.length_m, 2),
              people_count=int(counts.get(rid, 0)), count_final=False, crowd_factor=None,
              est_time_s=None, polyline=[local_position(p) for p in r.path])
        for rid, r in world.routes.items()
    ]


def scored_routes(world, scores: dict, local_position) -> list:
    """Node 2's /route_scores -> final Route list in world (routes.yaml) order (§6.2).
    Routes Node 2 did not score are left out; unknown names are ignored."""
    routes = []
    for rid, r in world.routes.items():
        s = scores.get(rid)
        if not isinstance(s, dict):
            continue
        distance_m = float(s.get("distanceM", r.length_m))
        people_count = int(s.get("peopleCount", 0))
        # The teammate UI used a 7 m/s scoring constant, while the real cart was much
        # slower.  For display / comparison, use the actual integration target speed and a crowd-clearance delay aligned with route choice.
        est_time_s = distance_m / ROUTE_CART_SPEED_MPS + people_count * CROWD_DELAY_S_PER_PERSON
        routes.append(Route(
            id=rid, label=_label(r), length_m=distance_m,
            people_count=people_count, count_final=True, crowd_factor=None,
            est_time_s=round(est_time_s, 2),
            polyline=[local_position(p) for p in r.path]))
    return routes


def local_m(msg: dict) -> tuple:
    p = msg.get("localM") or {}
    return float(p["e"]), float(p["n"])


def drone_position(msg: dict) -> dict:
    """/drone/position (§4.9)."""
    return {"local": local_m(msg), "altitude_m": float(msg.get("altitudeM") or 0.0),
            "heading_deg": float(msg.get("headingDeg") or 0.0) % 360.0,
            "speed_mps": float(msg.get("speedMps") or 0.0)}


def drone_status(msg: dict) -> dict:
    """/drone/status (§4.8): the fields the backend uses."""
    payload = msg.get("payloadState") or {}
    return {
        "mission_id": msg.get("missionId"),
        "state": msg.get("state") or "offline",
        "docked": bool(msg.get("dockedOnCart")),
        "battery_pct": msg.get("batteryPct"),
        "flight_time_left_s": msg.get("flightTimeLeftS"),
        "link": msg.get("link") or "ok",
        "camera_mode": msg.get("cameraMode"),
        "payload": (bool(payload["speaker"]), bool(payload["led"]))
        if {"speaker", "led"} <= set(payload) else None,
        "scan": msg.get("scan"),
        "fault": msg.get("fault"),
    }


def cart_pose(msg: dict) -> dict:
    """/cart/pose (§4.13)."""
    return {"local": local_m(msg), "heading_deg": float(msg.get("headingDeg") or 0.0) % 360.0,
            "speed_kmh": float(msg.get("speedKmh") or 0.0)}


def cart_status(msg: dict) -> dict:
    """/cart/status (§4.14): the fields the backend uses."""
    rescue = msg.get("rescue") or {}
    return {"mission_id": msg.get("missionId"), "state": msg.get("state") or "at_hub",
            "ret_phase": msg.get("retPhase"), "rescue_progress": rescue.get("progress") or "none",
            "route": msg.get("route"), "leg": msg.get("leg")}
