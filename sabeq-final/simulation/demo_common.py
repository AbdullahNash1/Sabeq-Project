"""
demo_common.py - shared helpers for sim_node.py (venv) and mission.py (no venv).
Standard library only, so both environments can import it.
Keep this file in ~/emergency_drone next to the other scripts.
"""
import json
import math
from pathlib import Path

META_PATH = Path.home() / "emergency_drone/world_meta.json"

STATUS_PORT = 5601  # sim_node -> mission  (counts, cart progress, rescue state)
CMD_PORT = 5602     # mission  -> sim_node (scan info, cart start, siren)
POSE_PORT = 5603    # sim_node -> pose_sender (model poses to apply in Gazebo)

CART_STOP_BEFORE = 5.0   # cart stops this many metres before the incident centre

# Rescue geometry - keep in sync with gen_world.py (golf cart bed, stretcher, medics)
BED_X = -2.3             # stretcher centre on the cart's rear bed (cart frame, m)
BED_TOP = 0.72           # bed surface height (m)
MEDIC_RIDE = ((-1.55, 0.5), (-3.05, -0.5))   # medics standing on the bed (cart frame)
MEDIC_SPACING = 1.35     # medics stand this far in front of / behind the stretcher centre
CARRY_Z = 0.45           # stretcher height while carried
STRETCHER_TOP = 0.36     # patient height above the stretcher model origin


def load_meta():
    if not META_PATH.exists():
        raise SystemExit(f"{META_PATH} not found. Run: python3 ~/emergency_drone/gen_world.py")
    return json.loads(META_PATH.read_text())


def build_path(meta, route):
    """Cart path: cart start -> route waypoints -> incident pad (ENU metres)."""
    start = tuple(meta["cart_start_enu"][:2])
    wps = [tuple(p) for p in meta["routes"][route]["waypoints_enu"]]
    return [start] + wps + [tuple(meta["incident_enu"])]


def build_return_path(meta, route):
    """Cart path back: from where it stopped at the incident, back along the same
    route to the start, then to the First Aid Center door."""
    rev = build_path(meta, route)[::-1]
    x, y, _ = along(rev, CART_STOP_BEFORE)
    out, acc = [(x, y)], 0.0
    for (x0, y0), (x1, y1) in zip(rev, rev[1:]):
        acc += math.hypot(x1 - x0, y1 - y0)
        if acc > CART_STOP_BEFORE + 1e-6 and (x1, y1) != out[-1]:
            out.append((x1, y1))
    out += [tuple(p) for p in meta.get("first_aid", {}).get("cart_route_enu", [])]
    return out


def route_offset(meta, route):
    """Distance from cart start to the first route waypoint (route 's' -> path 's')."""
    sx, sy = meta["cart_start_enu"][:2]
    wx, wy = meta["routes"][route]["waypoints_enu"][0]
    return math.hypot(wx - sx, wy - sy)


def path_length(path):
    return sum(math.hypot(x1 - x0, y1 - y0) for (x0, y0), (x1, y1) in zip(path, path[1:]))


def along(path, s):
    """(x, y, heading_enu_rad) at distance s along the path, clamped to its ends."""
    s = max(0.0, s)
    x1, y1, h = path[0][0], path[0][1], 0.0
    for (x0, y0), (x1, y1) in zip(path, path[1:]):
        L = math.hypot(x1 - x0, y1 - y0)
        if L < 1e-6:
            continue
        h = math.atan2(y1 - y0, x1 - x0)
        if s <= L:
            t = s / L
            return x0 + t * (x1 - x0), y0 + t * (y1 - y0), h
        s -= L
    return x1, y1, h


def cart_local(cart, lx, ly):
    """World (x, y) of a point given in the cart frame. cart = (x, y, heading)."""
    c, s = math.cos(cart[2]), math.sin(cart[2])
    return cart[0] + c * lx - s * ly, cart[1] + s * lx + c * ly


def wrap(a):
    return (a + math.pi) % (2 * math.pi) - math.pi


def enu_to_ned(x, y, home):
    """World ENU (x east, y north) -> PX4 local NED relative to the drone's spawn point."""
    return y - home[1], x - home[0]


def heading_to_yaw_ned_deg(h_enu_rad):
    return (90.0 - math.degrees(h_enu_rad)) % 360.0
