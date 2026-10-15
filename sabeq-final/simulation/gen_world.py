#!/usr/bin/env python3
"""
Generate the expo_demo world for PX4 SITL + Gazebo Harmonic.

Just the routes (light enough for a laptop iGPU):
  - main entrance avenue -> start plaza with the golf cart + drone at (0, 0)
  - First Aid Center beside the start plaza, two medics (hi-vis vests) + stretcher
  - 3 avenues fan out from the start plaza to the Incident Plaza at (0, 112):
      A_west  : north-west, then north, then into the plaza   (90 people)
      B_north : straight central avenue - shortest            (125 people)
      C_east  : north-east, then north, then into the plaza   (60 people)
  - everybody stands ON the avenues as an obstacle and only steps aside when the
    drone is overhead; a small circle of onlookers surrounds the injured visitor and
    opens up when the drone arrives over them
  - between the avenues: gardens with shade canopies, fountains, food kiosks, trees;
    palms and lamps along the avenues; nothing outside the routes; shadows off
  - a bright halo + arrow floats above the drone so it is easy to spot

Only the people the scripts move (route crowds, medics, patient) are Fuel models.
Everything else is generated locally: procedural PNG textures + OBJ meshes merged
per material (~45 draw calls for the whole site), so the world loads fast and
renders well on an Intel iGPU. Assets go to ~/emergency_drone/expo_assets.

Contract for the other scripts (sim_node.py, mission.py, demo_common.py):
  world_meta.json (routes, people, first_aid, patient_enu, rescue_models),
  model names "golf_cart", "person_<route>_<nn>", "medic_0", "medic_1",
  "stretcher", "patient"; hover point = cart start; routes >= 30 deg apart;
  crowds 15-36 m away; nothing taller than ~7 m within 6 m of any cart path.
  The drone spawn pose printed at the end goes into launch_sim.sh (DRONE_POSE).
  The ambient crowd near the start is shaped like the route crowds (dense toward
  B, medium toward A, almost none toward C), so it never changes the scan result.
All walkable surfaces sit just below z = 0, so people moved with z = 0
by pose_sender.py stand on top of the paving.

People models are loaded from the LOCAL Fuel cache (~/.gz/fuel/...).

Usage (stdlib only, no venv):
  python3 gen_world.py              # full world
  python3 gen_world.py --test       # detection test: 4 people 8 m in front of the drone
  python3 gen_world.py --no-people  # layout only
  python3 gen_world.py --lite       # fewer trees, palms and lamps
  python3 gen_world.py --shadows    # shadows on (prettier, slower)
  python3 gen_world.py --fuel       # detailed Fuel people in the scan zone (much slower)
  python3 gen_world.py --rebuild-textures
"""
import argparse
import json
import math
import random
import re
import struct
import zlib
from pathlib import Path

PX4 = Path.home() / "PX4-Autopilot"
WORLDS = PX4 / "Tools/simulation/gz/worlds"
SRC = WORLDS / "default.sdf"
OUT = WORLDS / "expo_demo.sdf"
META = Path.home() / "emergency_drone/world_meta.json"
ASSETS = Path.home() / "emergency_drone/expo_assets"
TEX_VERSION = "3"
WORLD_NAME = "expo_demo"  # must match file name for PX4

FUEL_CACHE = Path.home() / ".gz/fuel/fuel.gazebosim.org/openrobotics/models"
LINKS = Path.home() / "emergency_drone/models"  # space-free symlinks to cached models
# Static models only. "Male visitor" is an animated actor that sinks into the ground.
PEOPLE = ["Standing person", "Walking person", "Casual female"]

# ---- Layout (ENU: x = east, y = north, metres) ----------------------------
ROAD_W = 8.0                    # avenue width (the part the cart drives on)
WALK_W = 4.0                    # sidewalk width each side
SITE = (-95.0, -32.0, 95.0, 150.0)   # perimeter wall: xmin, ymin, xmax, ymax
START = (0.0, 0.0)              # cart start (start plaza centre)
START_R = 12.0                  # start plaza radius
INCIDENT = (0.0, 112.0)         # Incident Plaza centre (injured visitor)
INCIDENT_R = 14.0
ENTRANCE = [(0.0, SITE[1]), START]   # entrance avenue from the south gate

ROUTES = {  # the cart then drives from the last waypoint to the incident
    # people: (in the scan zone - Fuel models YOLO counts, whole avenue in total)
    "A_west":  {"wp": [START, (-36, 36), (-36, 92), (-16, 112)], "people": (18, 90)},
    "B_north": {"wp": [START, (0, 98)],                          "people": (26, 125)},
    "C_east":  {"wp": [START, (36, 36), (36, 92), (16, 112)],    "people": (10, 60)},
}
# onlookers in closed rings round the injured visitor (radius, count); they only move for the drone
INCIDENT_RINGS = [(2.2, 9)]     # one small circle
GROUP_LEN = 5.0             # people beyond the scan zone are merged into groups (one model each)
GROUP_MAX = 6
MARKER_DZ = 1.4             # the bright marker floats this far above the drone
CROWD_RANGE = (15.0, 36.0)  # metres along each route where people are placed
MIN_GAP = 0.8               # min distance between people
CLEAR_MARGIN = 1.5          # how far past the road edge people step when cleared
SEED = 7
AMBIENT = 1200              # spectators standing at the kerb of the avenues (600 with --lite)
WALK_DENS = 1 / 9.0         # walkers per metre of walking loop (half with --lite)
WALK_LANES = (6.8, 7.6)     # sidewalk walking lanes (offset from the avenue centre line)
PALM_OFF = 7.0              # palms + lamps at the outer edge of the sidewalk (people clear to 5.5 m)
FOUNTAIN_Y = 38.0           # fountains in the gardens between the avenues (x = +-18)
SHAPE_M = 80.0              # within this distance of the hover point, the ambient crowd ...
SHAPE = {"A_west": 0.65, "B_north": 1.0, "C_east": 0.35}  # ... follows the route crowd order
HOVER_POINT = START         # drone scans from here (right above the cart)
PATIENT = (INCIDENT[0], INCIDENT[1], 0.0)   # injured visitor lying down: x, y, yaw
CART_POSE = (0.0, 0.0, math.pi / 2)   # x, y, yaw (facing north, toward the avenues)
CART_ROOF_TOP = 2.03
DRONE_SPAWN_X_OFF = -0.1
DRONE_SPAWN_Z = CART_ROOF_TOP + 0.25
LEAD = 15.0                 # drone flies this far ahead of the cart (for checks)

# First Aid Center (east of the entrance avenue, beside the start plaza).
# Keep in sync with the rescue constants in demo_common.py.
FA_C, FA_SIZE, FA_H = (17.0, -15.0), (14.0, 11.0), 5.5   # front (door) faces west
FA_DOOR = (8.6, -15.0)           # medics' formation centre outside the door
FA_INSIDE = (14.5, -15.0)        # inside the building (out of sight)
MEDIC_RUN = [FA_DOOR, (3.6, -8.0)]     # then to the back of the cart
CART_FA_ROUTE = [(0.0, -15.0)]   # cart: start -> here (stops beside the door)


def _incident_corridors():
    out = []
    for r in ROUTES.values():
        a = r["wp"][-1]
        dx, dy = INCIDENT[0] - a[0], INCIDENT[1] - a[1]
        L = math.hypot(dx, dy)
        out.append(((INCIDENT[0] - dx / L * 13, INCIDENT[1] - dy / L * 13), INCIDENT, 3.0))
    return out


CORRIDORS = [  # kept clear of props and visitors: (a, b, half-width)
    (FA_DOOR, MEDIC_RUN[1], 2.0), (MEDIC_RUN[1], (0.0, -4.6), 2.0),
    (START, CART_FA_ROUTE[0], 2.4), ((0.0, -12.5), FA_DOOR, 2.0), (FA_DOOR, FA_INSIDE, 1.5),
] + _incident_corridors()

# Surface heights (top faces). All below 0: people/cart are posed at z = 0.
Z_DESERT, Z_PLOT, Z_GRASS, Z_WALK = -0.060, -0.050, -0.044, -0.036
Z_STREET, Z_JUNC, Z_PLAZA, Z_DECAL = -0.026, -0.016, -0.010, -0.004


def scene_xml(shadows=True):
    return ("<scene><ambient>0.62 0.62 0.60 1</ambient>"
            "<background>0.64 0.76 0.88 1</background>"
            f"<shadows>{'true' if shadows else 'false'}</shadows><grid>false</grid><sky></sky></scene>")


# ---- Materials --------------------------------------------------------------
# tex: texture name (generated), color: albedo multiplier / flat colour
MATS = {
    "desert": dict(tex="sand", shadow=False), "plot": dict(tex="plot", shadow=False),
    "grass": dict(tex="grass", shadow=False), "sidewalk": dict(tex="sidewalk", shadow=False),
    "street": dict(tex="street", shadow=False), "junction": dict(tex="junction", shadow=False),
    "promenade": dict(tex="promenade", shadow=False), "plaza": dict(tex="plaza", shadow=False),
    "asphalt": dict(tex="asphalt", shadow=False), "water": dict(tex="water", rough=0.12, shadow=False),
    "incident": dict(tex="incident", shadow=False), "dronepad": dict(tex="dronepad", shadow=False),
    "medsign": dict(tex="medsign"), "glass": dict(tex="f_glass", rough=0.25),
    "stone": dict(tex="f_stone"), "screen": dict(tex="f_screen"), "panel": dict(tex="f_panel"),
    "roof": dict(tex="roof"), "lattice": dict(tex="lattice", rough=0.3),
    "white": dict(color=(0.94, 0.93, 0.90)), "kerb": dict(color=(0.74, 0.72, 0.68)),
    "metal": dict(color=(0.24, 0.25, 0.27), rough=0.5), "wood": dict(color=(0.50, 0.34, 0.20)),
    "trunk": dict(color=(0.38, 0.28, 0.19)), "palm_trunk": dict(color=(0.52, 0.42, 0.30)),
    "frond": dict(color=(0.32, 0.50, 0.17)), "leaf_a": dict(color=(0.24, 0.45, 0.18)),
    "leaf_b": dict(color=(0.38, 0.54, 0.20)), "shrub": dict(color=(0.20, 0.38, 0.15)),
    "teal": dict(color=(0.07, 0.55, 0.56)), "gold": dict(color=(0.88, 0.64, 0.16)),
    "terracotta": dict(color=(0.76, 0.33, 0.20)), "green": dict(color=(0.08, 0.50, 0.28)),
    "red": dict(color=(0.82, 0.08, 0.10)), "orange": dict(color=(0.96, 0.45, 0.06)),
    "pink": dict(color=(0.90, 0.42, 0.58)), "yellow": dict(color=(0.96, 0.82, 0.22)),
}
FACADE_TILE = {"glass": (3, 4), "stone": (4, 4), "screen": (3, 3), "panel": (4, 4)}
BANNERS = ["teal", "gold", "terracotta", "green"]


# =============================================================================
# Procedural textures (stdlib only, cached)
# =============================================================================
def _hash(a, b, s=0):
    h = (a * 374761393 + b * 668265263 + s * 1442695041) & 0xFFFFFFFF
    h = ((h ^ (h >> 13)) * 1274126177) & 0xFFFFFFFF
    return ((h ^ (h >> 16)) & 0xFFFF) / 65535.0


def _vnoise(n, cells, rng):
    g = [[rng.random() for _ in range(cells)] for _ in range(cells)]
    idx = []
    for x in range(n):
        f = x * cells / n
        i = int(f)
        t = f - i
        idx.append((i % cells, (i + 1) % cells, t * t * (3 - 2 * t)))
    out = [0.0] * (n * n)
    for y in range(n):
        a0, a1, u = idx[y]
        r0, r1 = g[a0], g[a1]
        b = y * n
        for x, (i0, i1, t) in enumerate(idx):
            top = r0[i0] + (r0[i1] - r0[i0]) * t
            bot = r1[i0] + (r1[i1] - r1[i0]) * t
            out[b + x] = top + (bot - top) * u
    return out


def _fbm(n, rng, octs):
    tot = [0.0] * (n * n)
    s = 0.0
    for cells, amp in octs:
        v = _vnoise(n, cells, rng)
        s += amp
        for i in range(n * n):
            tot[i] += v[i] * amp
    return [t / s for t in tot]


def _grain(n, rng):
    return [rng.random() for _ in range(n * n)]


def _png(path, n, fn):
    rows = []
    for y in range(n):
        row = bytearray(1 + n * 3)
        j = 1
        for x in range(n):
            for v in fn(x, y):
                v = int(v * 255.0 + 0.5)
                row[j] = 0 if v < 0 else (255 if v > 255 else v)
                j += 1
        rows.append(bytes(row))

    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    data = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", n, n, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows), 6)) + chunk(b"IEND", b""))
    path.write_bytes(data)


def _pavers(n, rng, cw, ch, tones, jcol, bond=None, joint=1, var=0.06, grain=0.08, dirt=0.10):
    f = _fbm(n, rng, [(4, 0.6), (16, 0.4)])
    g = _grain(n, rng)
    bond = cw // 2 if bond is None else bond

    def fn(x, y):
        row = y // ch
        xs = (x + (row % 2) * bond) % n
        col = xs // cw
        jx, jy = xs % cw, y % ch
        i = y * n + x
        if jx < joint or jy < joint:
            c, s = jcol, 1 + grain * (g[i] - 0.5)
        else:
            c = tones[int(_hash(row, col, 7) * len(tones)) % len(tones)]
            s = 1 + var * (2 * _hash(row, col) - 1) + grain * (g[i] - 0.5) + dirt * (f[i] - 0.5)
            if jx == joint or jy == joint:
                s *= 0.94
        return c[0] * s, c[1] * s, c[2] * s
    return fn


def t_sand(rng, n):
    f = _fbm(n, rng, [(3, 0.5), (9, 0.3), (27, 0.2)])
    g = _grain(n, rng)

    def fn(x, y):
        i = y * n + x
        s = 0.82 + 0.30 * (f[i] - 0.5) + 0.10 * (g[i] - 0.5)
        return 0.90 * s, 0.77 * s, 0.58 * s
    return fn


def t_grass(rng, n):
    f = _fbm(n, rng, [(4, 0.5), (12, 0.3), (40, 0.2)])
    g = _grain(n, rng)

    def fn(x, y):
        i = y * n + x
        t = f[i]
        s = 1 + 0.30 * (g[i] - 0.5)
        return ((0.30 + 0.14 * t) * s, (0.47 + 0.10 * t) * s, (0.20 + 0.05 * t) * s)
    return fn


def t_flat(rng, n, col, amp_f=0.08, amp_g=0.14):
    f = _fbm(n, rng, [(4, 0.6), (16, 0.4)])
    g = _grain(n, rng)

    def fn(x, y):
        i = y * n + x
        s = 1 + amp_f * (f[i] - 0.5) + amp_g * (g[i] - 0.5)
        return col[0] * s, col[1] * s, col[2] * s
    return fn


def t_street(rng, n):
    # u = 8 m along the street, v = full 8 m carriageway width
    light = _pavers(n, rng, 32, 16, [(0.82, 0.80, 0.76), (0.79, 0.77, 0.73), (0.84, 0.82, 0.78)],
                    (0.62, 0.60, 0.57))
    edge = _pavers(n, rng, 16, 13, [(0.48, 0.48, 0.50), (0.44, 0.44, 0.46)], (0.34, 0.34, 0.36))
    mid = _pavers(n, rng, 32, 16, [(0.60, 0.58, 0.55), (0.56, 0.54, 0.51)], (0.44, 0.43, 0.41), bond=16)

    def fn(x, y):
        if y < 14 or y >= 242:
            return edge(x, y)
        if 120 <= y < 136:
            return mid(x, y)
        return light(x, y)
    return fn


def t_promenade(rng, n):
    slab = _pavers(n, rng, 32, 16, [(0.86, 0.70, 0.52), (0.82, 0.66, 0.48), (0.88, 0.74, 0.57)],
                   (0.62, 0.52, 0.42))
    band = _pavers(n, rng, 16, 16, [(0.93, 0.89, 0.80), (0.90, 0.86, 0.77)], (0.70, 0.66, 0.60), bond=0)
    g = _grain(n, rng)

    def fn(x, y):
        if y < 18 or y >= 238:
            return band(x, y)
        if 96 <= y < 160:
            if (x + y) % 32 == 0 or (x - y) % 32 == 0:
                return 0.62, 0.55, 0.48
            d = (((x + y) // 32) + ((x - y) // 32)) % 2
            c = (0.93, 0.89, 0.80) if d else (0.74, 0.40, 0.28)
            s = 1 + 0.08 * (g[y * n + x] - 0.5)
            return c[0] * s, c[1] * s, c[2] * s
        return slab(x, y)
    return fn


def t_plaza(rng, n):
    g = _grain(n, rng)
    h = n / 2
    sect = 2 * math.pi / 16

    def fn(x, y):
        dx, dy = (x + 0.5) / h - 1, (y + 0.5) / h - 1
        r = math.hypot(dx, dy)
        th = math.atan2(dy, dx)
        if r > 0.95:
            c = (0.52, 0.50, 0.48)
        elif r > 0.91:
            c = (0.93, 0.90, 0.83)
        elif r > 0.62:
            c = (0.87, 0.80, 0.68) if int((th + math.pi) / sect) % 2 else (0.81, 0.74, 0.62)
        elif r > 0.36:
            c = (0.90, 0.86, 0.78)
        else:
            c = (0.93, 0.90, 0.84)
        if abs(r - 0.62) < 0.007 or abs(r - 0.36) < 0.007 or abs(r - 0.91) < 0.005:
            c = (0.56, 0.53, 0.49)
        elif 0.36 < r < 0.91:
            a = th % sect
            if min(a, sect - a) * r < 0.0055:
                c = (0.97, 0.95, 0.90)
        s = 1 + 0.07 * (g[y * n + x] - 0.5)
        if (r * 28) % 1 < 0.05:
            s *= 0.93
        return c[0] * s, c[1] * s, c[2] * s
    return fn


def t_incident(rng, n):
    g = _grain(n, rng)
    h = n / 2

    def fn(x, y):
        dx, dy = (x + 0.5) / h - 1, (y + 0.5) / h - 1
        r = math.hypot(dx, dy)
        th = math.atan2(dy, dx)
        if r > 0.84:
            c = (0.85, 0.10, 0.10) if int((th + math.pi) / (2 * math.pi) * 20 + r * 3) % 2 else (0.96, 0.96, 0.94)
        elif r > 0.78:
            c = (0.96, 0.96, 0.94)
        elif (abs(dx) < 0.13 and abs(dy) < 0.52) or (abs(dy) < 0.13 and abs(dx) < 0.52):
            c = (0.97, 0.97, 0.95)
        else:
            c = (0.80, 0.10, 0.10)
        s = 1 + 0.06 * (g[y * n + x] - 0.5)
        return c[0] * s, c[1] * s, c[2] * s
    return fn


def t_dronepad(rng, n):
    g = _grain(n, rng)
    h = n / 2

    def fn(x, y):
        dx, dy = (x + 0.5) / h - 1, (y + 0.5) / h - 1
        r = math.hypot(dx, dy)
        if 0.86 < r < 0.94 or (abs(abs(dx) - 0.28) < 0.065 and abs(dy) < 0.42) or (abs(dx) < 0.28 and abs(dy) < 0.06):
            c = (0.96, 0.96, 0.94)
        elif 0.72 < r < 0.82:
            c = (0.10, 0.60, 0.38)
        else:
            c = (0.24, 0.26, 0.29)
        s = 1 + 0.08 * (g[y * n + x] - 0.5)
        return c[0] * s, c[1] * s, c[2] * s
    return fn


def t_medsign(rng, n):
    h = n / 2

    def fn(x, y):
        dx, dy = (x + 0.5) / h - 1, (y + 0.5) / h - 1
        if max(abs(dx), abs(dy)) > 0.9:
            return 0.82, 0.08, 0.10
        if (abs(dx) < 0.17 and abs(dy) < 0.62) or (abs(dy) < 0.17 and abs(dx) < 0.62):
            return 0.82, 0.08, 0.10
        return 0.97, 0.97, 0.96
    return fn


def t_glass(rng, n):
    f = _fbm(n, rng, [(2, 0.5), (8, 0.5)])

    def fn(x, y):
        if x % 128 < 5:
            return 0.82, 0.84, 0.85
        if y < 16 or y >= 240:
            return 0.70, 0.72, 0.74
        if 124 <= y < 128:
            return 0.78, 0.80, 0.81
        t = y / n
        k = 0.05 * _hash(x // 128, y // 128, 3) + 0.06 * (f[y * n + x] - 0.5)
        return 0.20 + 0.16 * t + k, 0.33 + 0.16 * t + k, 0.42 + 0.14 * t + k
    return fn


def t_stone(rng, n):
    f = _fbm(n, rng, [(4, 0.5), (16, 0.5)])
    g = _grain(n, rng)

    def fn(x, y):
        i = y * n + x
        s = 1 + 0.12 * (f[i] - 0.5) + 0.07 * (g[i] - 0.5)
        if 112 <= x < 144 and 56 <= y < 200:
            return 0.13, 0.11, 0.10
        if 106 <= x < 150 and 50 <= y < 206:
            return 0.90 * s, 0.82 * s, 0.68 * s
        yy = y - 216
        if 0 <= yy < 18 and abs(x % 32 - 16) < (18 - yy) * 0.8:
            return 0.42 * s, 0.33 * s, 0.25 * s
        if y % 32 == 0:
            s *= 0.86
        return 0.80 * s, 0.68 * s, 0.52 * s
    return fn


def t_screen(rng, n):
    g = _grain(n, rng)

    def fn(x, y):
        if x % 128 < 3 or y % 128 < 3:
            return 0.92, 0.90, 0.86
        d = abs(x % 32 - 16) + abs(y % 32 - 16)
        if d < 8:
            return 0.20, 0.12, 0.10
        s = 1 + 0.08 * (g[y * n + x] - 0.5)
        if d < 10:
            return 0.88 * s, 0.58 * s, 0.42 * s
        return 0.76 * s, 0.40 * s, 0.28 * s
    return fn


def t_panel(rng, n):
    g = _grain(n, rng)

    def fn(x, y):
        if 92 <= y < 96 or 160 <= y < 164:
            return 0.70, 0.70, 0.70
        if 96 <= y < 160:
            if x % 64 < 4:
                return 0.80, 0.80, 0.80
            t = (y - 96) / 64
            return 0.20 + 0.10 * t, 0.28 + 0.10 * t, 0.36 + 0.08 * t
        s = 1 + 0.05 * (g[y * n + x] - 0.5)
        if x % 64 == 0 or y % 128 == 0:
            s *= 0.82
        return 0.93 * s, 0.91 * s, 0.86 * s
    return fn


def t_water(rng, n):
    f = _fbm(n, rng, [(4, 0.4), (12, 0.35), (32, 0.25)])

    def fn(x, y):
        v = f[y * n + x]
        hl = 0.10 if 0.62 < v < 0.66 else 0.0
        return 0.14 + 0.10 * v + hl, 0.42 + 0.12 * v + hl, 0.56 + 0.10 * v + hl
    return fn


def t_lattice(rng, n):
    def fn(x, y):
        if x % 64 < 4 or y % 64 < 4 or (x + y) % 64 < 3 or (x - y) % 64 < 3:
            return 0.95, 0.95, 0.93
        t = y / n
        return 0.30 + 0.12 * t, 0.56 + 0.10 * t, 0.58 + 0.10 * t
    return fn


TEXTURES = {
    "sand": (t_sand, 256), "grass": (t_grass, 256),
    "plot": (lambda r, n: _pavers(n, r, 64, 64, [(0.88, 0.82, 0.70), (0.86, 0.79, 0.67), (0.90, 0.85, 0.74)],
                                  (0.70, 0.64, 0.55), bond=0, joint=2), 256),
    "sidewalk": (lambda r, n: _pavers(n, r, 32, 16, [(0.76, 0.58, 0.44), (0.70, 0.52, 0.40), (0.80, 0.64, 0.50)],
                                      (0.58, 0.52, 0.46)), 256),
    "junction": (lambda r, n: _pavers(n, r, 32, 32, [(0.80, 0.78, 0.74), (0.76, 0.74, 0.70)],
                                      (0.60, 0.58, 0.55), bond=0), 256),
    "street": (t_street, 256), "promenade": (t_promenade, 256), "plaza": (t_plaza, 512),
    "incident": (t_incident, 512), "dronepad": (t_dronepad, 256), "medsign": (t_medsign, 128),
    "f_glass": (t_glass, 256), "f_stone": (t_stone, 256), "f_screen": (t_screen, 256),
    "f_panel": (t_panel, 256), "roof": (lambda r, n: t_flat(r, n, (0.80, 0.80, 0.78)), 256),
    "asphalt": (lambda r, n: t_flat(r, n, (0.25, 0.25, 0.26), 0.10, 0.25), 256),
    "water": (t_water, 256), "lattice": (t_lattice, 256),
}


def build_textures(force=False):
    tdir = ASSETS / "textures"
    tdir.mkdir(parents=True, exist_ok=True)
    stamp = tdir / "VERSION"
    fresh = force or not stamp.exists() or stamp.read_text().strip() != TEX_VERSION
    missing = [k for k in TEXTURES if not (tdir / f"{k}.png").exists()]
    if not fresh and not missing:
        return tdir
    names = list(TEXTURES) if fresh else missing
    print(f"Generating {len(names)} textures (one-time, ~20 s) ...")
    for i, name in enumerate(names):
        fn, n = TEXTURES[name]
        _png(tdir / f"{name}.png", n, fn(random.Random(1000 + i), n))
    stamp.write_text(TEX_VERSION)
    return tdir


# =============================================================================
# Mesh builder (merged per material, written as OBJ)
# =============================================================================
def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _unit(a):
    L = math.sqrt(_dot(a, a)) or 1.0
    return (a[0] / L, a[1] / L, a[2] / L)


class Mesh:
    def __init__(self):
        self.v, self.t, self.n, self.f = [], [], [], []

    def poly(self, pts, uvs, n):
        cr = _cross(_sub(pts[1], pts[0]), _sub(pts[2], pts[0]))
        if _dot(cr, cr) < 1e-12:
            return
        if _dot(cr, n) < 0:
            pts, uvs = pts[::-1], uvs[::-1]
        self.n.append(_unit(n))
        self.f.append((len(self.v) + 1, len(pts), len(self.n)))
        self.v.extend(pts)
        self.t.extend(uvs)

    def tris(self):
        return sum(k - 2 for _, k, _ in self.f)

    def write(self, path):
        L = ["# gen_world.py"]
        L += [f"v {x:.3f} {y:.3f} {z:.3f}" for x, y, z in self.v]
        L += [f"vt {u:.4f} {v:.4f}" for u, v in self.t]
        L += [f"vn {x:.3f} {y:.3f} {z:.3f}" for x, y, z in self.n]
        for i0, k, ni in self.f:
            for j in range(1, k - 1):
                a, b, c = i0, i0 + j, i0 + j + 1
                L.append(f"f {a}/{a}/{ni} {b}/{b}/{ni} {c}/{c}/{ni}")
        path.write_text("\n".join(L) + "\n")


class Scene:
    def __init__(self):
        self.meshes = {}
        self.coll = []      # (cx, cy, z0, sx, sy, sz, yaw)
        self.props = []     # (x, y, r) occupied spots for spacing
        self.reserved = []  # people + their clear spots
        self.palms = 0
        self.crowd = 0
        self.hot = []       # (x, y, radius, weight) crowd attractions
        self.water = []     # (cx, cy, rx, ry) ellipses
        self.walk = []      # (a, b) segments walkers use - kept clear of props

    def m(self, mat):
        return self.meshes.setdefault(mat, Mesh())


S = Scene()
UP = (0.0, 0.0, 1.0)


def gpoly(mat, pts2, z, T=4.0, uvf=None):
    uvf = uvf or (lambda x, y: (x / T, y / T))
    S.m(mat).poly([(x, y, z) for x, y in pts2], [uvf(x, y) for x, y in pts2], UP)


def rect(mat, x0, y0, x1, y1, z, T=4.0):
    gpoly(mat, [(x0, y0), (x1, y0), (x1, y1), (x0, y1)], z, T)


def strip(mat, a, b, w, z, T=4.0, along=None):
    dx, dy = b[0] - a[0], b[1] - a[1]
    L = math.hypot(dx, dy)
    ux, uy = dx / L, dy / L
    px, py = -uy * w / 2, ux * w / 2
    pts = [(a[0] - px, a[1] - py), (b[0] - px, b[1] - py), (b[0] + px, b[1] + py), (a[0] + px, a[1] + py)]
    if along:
        uvs = [(0, 0), (L / along, 0), (L / along, 1), (0, 1)]
        S.m(mat).poly([(x, y, z) for x, y in pts], uvs, UP)
    else:
        gpoly(mat, pts, z, T)


def disk(mat, cx, cy, r, z, seg=48, unit=False, T=4.0, ky=1.0):
    m = S.m(mat)
    for i in range(seg):
        a0, a1 = 2 * math.pi * i / seg, 2 * math.pi * (i + 1) / seg
        pts = [(cx, cy, z), (cx + r * math.cos(a0), cy + r * ky * math.sin(a0), z),
               (cx + r * math.cos(a1), cy + r * ky * math.sin(a1), z)]
        if unit:
            uvs = [((p[0] - cx) / (2 * r) + 0.5, (p[1] - cy) / (2 * r * ky) + 0.5) for p in pts]
        else:
            uvs = [(p[0] / T, p[1] / T) for p in pts]
        m.poly(pts, uvs, UP)


def annulus(mat, cx, cy, r0, r1, z, seg=64, T=4.0, ky=1.0):
    m = S.m(mat)
    for i in range(seg):
        a0, a1 = 2 * math.pi * i / seg, 2 * math.pi * (i + 1) / seg
        pts = [(cx + r * math.cos(a), cy + r * ky * math.sin(a), z)
               for r, a in ((r0, a0), (r1, a0), (r1, a1), (r0, a1))]
        m.poly(pts, [(p[0] / T, p[1] / T) for p in pts], UP)


def obox(mat, c, ax, hs, tile=(2.0, 2.0), unit=False):
    m = S.m(mat)
    for i in range(3):
        j, k = [q for q in range(3) if q != i]
        for sign in (1, -1):
            n = tuple(ax[i][q] * sign for q in range(3))
            fc = tuple(c[q] + n[q] * hs[i] for q in range(3))
            pts, uvs = [], []
            for sj, sk in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
                pts.append(tuple(fc[q] + ax[j][q] * hs[j] * sj + ax[k][q] * hs[k] * sk for q in range(3)))
                if unit:
                    uvs.append(((sj + 1) / 2, (sk + 1) / 2))
                else:
                    uvs.append(((sj + 1) * hs[j] / tile[0], (sk + 1) * hs[k] / tile[1]))
            m.poly(pts, uvs, n)


def box(mat, cx, cy, z0, sx, sy, sz, yaw=0.0, tile=(2.0, 2.0), unit=False):
    c, s = math.cos(yaw), math.sin(yaw)
    obox(mat, (cx, cy, z0 + sz / 2), ((c, s, 0), (-s, c, 0), (0, 0, 1)), (sx / 2, sy / 2, sz / 2), tile, unit)


def seg_box(mat, p, q, w, t):
    u = _sub(q, p)
    L = math.sqrt(_dot(u, u))
    if L < 1e-6:
        return
    u = _unit(u)
    side = _cross(u, UP)
    side = _unit(side) if _dot(side, side) > 1e-6 else (1.0, 0.0, 0.0)
    up2 = _cross(side, u)
    c = tuple((p[i] + q[i]) / 2 for i in range(3))
    obox(mat, c, (u, side, up2), (L / 2, w / 2, t / 2))


def cyl(mat, cx, cy, z0, h, r0, r1=None, seg=12, top=True, inward=False, T=None, ky=1.0):
    r1 = r0 if r1 is None else r1
    m = S.m(mat)
    for i in range(seg):
        a0, a1 = 2 * math.pi * i / seg, 2 * math.pi * (i + 1) / seg
        am = (a0 + a1) / 2
        pts = [(cx + r0 * math.cos(a0), cy + r0 * ky * math.sin(a0), z0),
               (cx + r0 * math.cos(a1), cy + r0 * ky * math.sin(a1), z0),
               (cx + r1 * math.cos(a1), cy + r1 * ky * math.sin(a1), z0 + h),
               (cx + r1 * math.cos(a0), cy + r1 * ky * math.sin(a0), z0 + h)]
        sgn = -1 if inward else 1
        n = (sgn * math.cos(am), sgn * math.sin(am), sgn * (r0 - r1) / max(h, 1e-6))
        if T:
            uvs = [(a0 * r0 / T, 0), (a1 * r0 / T, 0), (a1 * r0 / T, h / T), (a0 * r0 / T, h / T)]
        else:
            uvs = [(i / seg, 0), ((i + 1) / seg, 0), ((i + 1) / seg, 1), (i / seg, 1)]
        m.poly(pts, uvs, n)
    if top:
        disk(mat, cx, cy, r1, z0 + h, seg=seg, T=T or 4.0, ky=ky)


def cone(mat, cx, cy, z0, h, r, seg=10):
    m = S.m(mat)
    apex = (cx, cy, z0 + h)
    for i in range(seg):
        a0, a1 = 2 * math.pi * i / seg, 2 * math.pi * (i + 1) / seg
        am = (a0 + a1) / 2
        p0 = (cx + r * math.cos(a0), cy + r * math.sin(a0), z0)
        p1 = (cx + r * math.cos(a1), cy + r * math.sin(a1), z0)
        sg = 1.0 if h >= 0 else -1.0
        m.poly([p0, p1, apex], [(0, 0), (1, 0), (0.5, 1)], (math.cos(am) * abs(h), math.sin(am) * abs(h), r * sg))
        m.poly([p0, p1, (cx, cy, z0)], [(0, 0), (1, 0), (0.5, 1)], (0, 0, -sg))


def _icosphere():
    t = (1 + 5 ** 0.5) / 2
    v = [_unit(p) for p in [(-1, t, 0), (1, t, 0), (-1, -t, 0), (1, -t, 0), (0, -1, t), (0, 1, t),
                            (0, -1, -t), (0, 1, -t), (t, 0, -1), (t, 0, 1), (-t, 0, -1), (-t, 0, 1)]]
    f = [(0, 11, 5), (0, 5, 1), (0, 1, 7), (0, 7, 10), (0, 10, 11), (1, 5, 9), (5, 11, 4), (11, 10, 2),
         (10, 7, 6), (7, 1, 8), (3, 9, 4), (3, 4, 2), (3, 2, 6), (3, 6, 8), (3, 8, 9), (4, 9, 5),
         (2, 4, 11), (6, 2, 10), (8, 6, 7), (9, 8, 1)]
    out = []
    for a, b, c in f:
        A, B, C = v[a], v[b], v[c]
        ab = _unit(tuple((A[i] + B[i]) / 2 for i in range(3)))
        bc = _unit(tuple((B[i] + C[i]) / 2 for i in range(3)))
        ca = _unit(tuple((C[i] + A[i]) / 2 for i in range(3)))
        out += [(A, ab, ca), (B, bc, ab), (C, ca, bc), (ab, bc, ca)]
    return out


ICO = _icosphere()


def blob(mat, c, r, kz=1.0):
    m = S.m(mat)
    for tri in ICO:
        pts = [(c[0] + p[0] * r, c[1] + p[1] * r, c[2] + p[2] * r * kz) for p in tri]
        n = tuple(sum(p[i] for p in tri) for i in range(3))
        m.poly(pts, [(0, 0), (1, 0), (0, 1)], (n[0], n[1], n[2] / max(kz, 0.1)))


def dome(mat, cx, cy, z0, r, kz, nlon=32, nlat=9, rep=(8, 3)):
    m = S.m(mat)

    def p(th, ph):
        return (cx + r * math.cos(ph) * math.cos(th), cy + r * math.cos(ph) * math.sin(th),
                z0 + r * kz * math.sin(ph))
    for j in range(nlat):
        ph0, ph1 = j / nlat * math.pi / 2, (j + 1) / nlat * math.pi / 2
        for i in range(nlon):
            th0, th1 = 2 * math.pi * i / nlon, 2 * math.pi * (i + 1) / nlon
            thm, phm = (th0 + th1) / 2, (ph0 + ph1) / 2
            n = (math.cos(phm) * math.cos(thm), math.cos(phm) * math.sin(thm), math.sin(phm) / kz)
            u0, u1 = i / nlon * rep[0], (i + 1) / nlon * rep[0]
            v0, v1 = j / nlat * rep[1], (j + 1) / nlat * rep[1]
            m.poly([p(th0, ph0), p(th1, ph0), p(th1, ph1), p(th0, ph1)],
                   [(u0, v0), (u1, v0), (u1, v1), (u0, v1)], n)


def arc_prism(mat, cx, cy, r0, r1, a0, a1, z0, z1, seg=18):
    m = S.m(mat)
    A = [a0 + (a1 - a0) * i / seg for i in range(seg + 1)]

    def P(r, a, z):
        return (cx + r * math.cos(a), cy + r * math.sin(a), z)
    for i in range(seg):
        b0, b1 = A[i], A[i + 1]
        bm = (b0 + b1) / 2
        m.poly([P(r0, b0, z1), P(r1, b0, z1), P(r1, b1, z1), P(r0, b1, z1)],
               [(b0 * r1 / 2, r0 / 2), (b0 * r1 / 2, r1 / 2), (b1 * r1 / 2, r1 / 2), (b1 * r1 / 2, r0 / 2)], UP)
        m.poly([P(r1, b0, z0), P(r1, b1, z0), P(r1, b1, z1), P(r1, b0, z1)],
               [(0, 0), (1, 0), (1, 1), (0, 1)], (math.cos(bm), math.sin(bm), 0))
        if r0 > 0.01:
            m.poly([P(r0, b0, z0), P(r0, b1, z0), P(r0, b1, z1), P(r0, b0, z1)],
                   [(0, 0), (1, 0), (1, 1), (0, 1)], (-math.cos(bm), -math.sin(bm), 0))
    for a, sgn in ((a0, -1), (a1, 1)):
        n = (-math.sin(a) * sgn, math.cos(a) * sgn, 0)
        m.poly([P(r0, a, z0), P(r1, a, z0), P(r1, a, z1), P(r0, a, z1)], [(0, 0), (1, 0), (1, 1), (0, 1)], n)


# =============================================================================
# Layout helpers
# =============================================================================
def d_seg(p, a, b):
    ax, ay = b[0] - a[0], b[1] - a[1]
    L2 = ax * ax + ay * ay
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * ax + (p[1] - a[1]) * ay) / L2))
    return math.hypot(p[0] - a[0] - t * ax, p[1] - a[1] - t * ay)


def _dedup(line):
    out = [tuple(line[0])]
    for p in line[1:]:
        if math.hypot(p[0] - out[-1][0], p[1] - out[-1][1]) > 1e-6:
            out.append(tuple(p))
    return out


# Avenues the cart drives on: the entrance avenue + each route up to the Incident Plaza
ROAD_LINES = [_dedup(ENTRANCE)] + [_dedup(list(r["wp"]) + [INCIDENT]) for r in ROUTES.values()]
ROAD_SEGS = [(a, b) for line in ROAD_LINES for a, b in zip(line, line[1:])]
ROAD_CORNERS = sorted({line[i] for line in ROAD_LINES for i in range(1, len(line) - 1)})
PLAZAS = [(START, START_R), (INCIDENT, INCIDENT_R)]


def cart_path(route):
    return _dedup([tuple(CART_POSE[:2])] + [tuple(p) for p in ROUTES[route]["wp"]] + [tuple(INCIDENT)])


def path_segs(path):
    return list(zip(path, path[1:]))


CART_SEGS = [s for r in ROUTES for s in path_segs(cart_path(r))]


def street_dist(p):
    return min(d_seg(p, a, b) for a, b in ROAD_SEGS)


def in_plaza(p, extra=0.0):
    return any(math.hypot(p[0] - c[0], p[1] - c[1]) < r + extra for c, r in PLAZAS)


def in_corridor(p, extra=0.0):
    return any(d_seg(p, a, b) < w + extra for a, b, w in CORRIDORS)


def in_coll(p, pad=0.0):
    for cx, cy, _, sx, sy, _, yaw in S.coll:
        c, s = math.cos(-yaw), math.sin(-yaw)
        dx, dy = p[0] - cx, p[1] - cy
        lx, ly = dx * c - dy * s, dx * s + dy * c
        if abs(lx) <= sx / 2 + pad and abs(ly) <= sy / 2 + pad:
            return True
    return False


def near_walk(p, d):
    return any(d_seg(p, a, b) < d for a, b in S.walk)


def in_water(p, pad=0.0):
    return any(((p[0] - cx) / (rx + pad)) ** 2 + ((p[1] - cy) / (ry + pad)) ** 2 < 1.0
               for cx, cy, rx, ry in S.water)


def try_place(p, r=1.0, street_m=None, plaza_ok=False, people_m=1.2):
    x, y = p
    street_m = ROAD_W / 2 + 1.5 if street_m is None else street_m
    if not (SITE[0] + 2 < x < SITE[2] - 2 and SITE[1] + 2 < y < SITE[3] - 2):
        return False
    if not plaza_ok and in_plaza(p, 0.8):
        return False
    if street_m > 0 and street_dist(p) < street_m:
        return False
    if any(math.hypot(x - qx, y - qy) < people_m for qx, qy in S.reserved):
        return False
    if in_corridor(p, r * 0.5):
        return False
    if near_walk(p, 0.65 + 0.5 * min(r, 1.5)):
        return False
    if any(math.hypot(x - qx, y - qy) < r + qr for qx, qy, qr in S.props):
        return False
    if in_coll(p, 0.8) or in_water(p, 0.5):
        return False
    S.props.append((x, y, r))
    return True


def along(wp, s):
    """Point and heading at distance s along a polyline."""
    x1, y1, h = wp[0][0], wp[0][1], 0.0
    for (x0, y0), (x1, y1) in zip(wp, wp[1:]):
        L = math.hypot(x1 - x0, y1 - y0)
        if L < 1e-6:
            continue
        h = math.atan2(y1 - y0, x1 - x0)
        if s <= L:
            t = s / L
            return x0 + t * (x1 - x0), y0 + t * (y1 - y0), h
        s -= L
    return x1, y1, h


def scan_bearings():
    hx, hy = HOVER_POINT
    out = {}
    for name, r in ROUTES.items():
        mx, my, _ = along(r["wp"], sum(CROWD_RANGE) / 2)
        out[name] = math.degrees(math.atan2(my - hy, mx - hx))
    return out


# =============================================================================
# Ground, avenues, plazas, perimeter
# =============================================================================
def kerbs():
    for (a, b) in ROAD_SEGS:
        others = [sg for sg in ROAD_SEGS if sg != (a, b)]
        L = math.hypot(b[0] - a[0], b[1] - a[1])
        ux, uy = (b[0] - a[0]) / L, (b[1] - a[1]) / L
        for side in (1, -1):
            off = ROAD_W / 2 + 0.1
            run, prev = None, None
            n = int(L / 0.25)
            for k in range(n + 2):
                t = min(k * 0.25, L)
                p = (a[0] + ux * t - uy * off * side, a[1] + uy * t + ux * off * side)
                ok = (k <= n and all(d_seg(p, *o) > ROAD_W / 2 + 0.3 for o in others)
                      and not in_plaza(p, 0.3)
                      and SITE[0] + 0.5 < p[0] < SITE[2] - 0.5 and SITE[1] + 0.5 < p[1] < SITE[3] - 0.5)
                if ok and run is None:
                    run = p
                if not ok and run is not None:
                    _kerb(run, prev)
                    run = None
                prev = p


def _kerb(a, b):
    L = math.hypot(b[0] - a[0], b[1] - a[1])
    if L > 0.3:
        box("kerb", (a[0] + b[0]) / 2, (a[1] + b[1]) / 2, -0.04, L + 0.05, 0.22, 0.14,
            yaw=math.atan2(b[1] - a[1], b[0] - a[0]))


def inner_poly(side):
    """Zone between the central avenue and the A (side=-1) or C (side=+1) avenue,
    inset by avenue half width + sidewalk. Works in u = |x| on that side."""
    o = ROAD_W / 2 + WALK_W
    k = o * math.sqrt(2)
    xin, xout = o, 36.0 - o
    u_cap = 128.0 - k - 96.0          # where the top diagonal (y = 128 - u) reaches y = 96
    pts = [(xin, xin + k), (xin, 96.0), (u_cap, 96.0), (xout, 128.0 - xout - k), (xout, xout + k)]
    return [(side * u, y) for u, y in pts]


def ground(lite):
    cx, cy = (SITE[0] + SITE[2]) / 2, (SITE[1] + SITE[3]) / 2
    rect("desert", cx - 700, cy - 700, cx + 700, cy + 700, Z_DESERT, T=6.0)
    rect("plot", SITE[0] + 0.3, SITE[1] + 0.3, SITE[2] - 0.3, SITE[3] - 0.3, Z_PLOT, T=5.12)
    # sidewalks + avenues; tiny per-segment height steps so overlaps never z-fight
    for i, (a, b) in enumerate(ROAD_SEGS):
        strip("sidewalk", a, b, ROAD_W + 2 * WALK_W, Z_WALK + 0.0004 * i, T=2.56)
        strip("street", a, b, ROAD_W, Z_STREET + 0.0005 * i, along=8.0)
    for p in ROAD_CORNERS:
        disk("sidewalk", *p, ROAD_W / 2 + WALK_W, Z_WALK + 0.006, seg=24, T=2.56)
        disk("junction", *p, ROAD_W / 2 + 0.6, Z_JUNC, seg=24, T=4.0)
    for c, r in PLAZAS:
        disk("plaza", *c, r, Z_PLAZA, seg=96, unit=True)
    kerbs()


def gates():
    x0, y0, x1, y1 = SITE
    return [(0.0, y0, True), (x0, 60.0, False), (x1, 60.0, False)]


def perimeter():
    x0, y0, x1, y1 = SITE
    gap = 11.0
    H, T = 1.8, 0.6
    gs = gates()
    sides = [((x0, y0), (x1, y0)), ((x0, y1), (x1, y1)), ((x0, y0), (x0, y1)), ((x1, y0), (x1, y1))]
    for a, b in sides:
        horiz = a[1] == b[1]
        lo, hi = (a[0], b[0]) if horiz else (a[1], b[1])
        cuts = sorted(g[0] if horiz else g[1] for g in gs
                      if (g[2] == horiz) and abs((g[1] if horiz else g[0]) - (a[1] if horiz else a[0])) < 1e-6)
        spans, s0 = [], lo
        for c in cuts:
            spans.append((s0, c - gap))
            s0 = c + gap
        spans.append((s0, hi))
        for s0, s1 in spans:
            mid, L = (s0 + s1) / 2, s1 - s0
            if horiz:
                box("plot", mid, a[1], -0.05, L, T, H, tile=(5.12, 5.12))
                S.coll.append((mid, a[1], 0, L, T, H, 0.0))
            else:
                box("plot", a[0], mid, -0.05, T, L, H, tile=(5.12, 5.12))
                S.coll.append((a[0], mid, 0, T, L, H, 0.0))
            k = 0.0
            while k <= L:
                p = s0 + k
                if horiz:
                    box("white", p, a[1], -0.05, 0.9, 0.9, H + 0.35)
                else:
                    box("white", a[0], p, -0.05, 0.9, 0.9, H + 0.35)
                k += 15.0
    for i, (gx, gy, horiz) in enumerate(gs):
        acc = BANNERS[i % 4]
        for s in (-1, 1):
            px, py = (gx + s * 9.5, gy) if horiz else (gx, gy + s * 9.5)
            box("stone", px, py, -0.05, 3.2, 3.2, 9.0, tile=(4, 4))
            box(acc, px, py, 6.6, 3.3, 3.3, 0.5)
            S.coll.append((px, py, 0, 3.2, 3.2, 9.0, 0.0))
        if horiz:
            box("white", gx, gy, 8.4, 22.5, 3.6, 1.4)
            box(acc, gx, gy, 8.2, 22.6, 3.7, 0.35)
        else:
            box("white", gx, gy, 8.4, 3.6, 22.5, 1.4)
            box(acc, gx, gy, 8.2, 3.7, 22.6, 0.35)
        S.hot.append((gx + (0 if horiz else (8 if gx < 0 else -8)), gy + (8 if horiz else 0), 6.0, 1.0))


# =============================================================================
# Buildings
# =============================================================================
FRONT = {"N": (0, 1), "S": (0, -1), "E": (1, 0), "W": (-1, 0)}


def _faces(cx, cy, sx, sy):
    """(face centre x, y, outward nx, ny, along ax, ay, length) for the 4 faces."""
    return [(cx, cy + sy / 2, 0, 1, 1, 0, sx), (cx, cy - sy / 2, 0, -1, 1, 0, sx),
            (cx + sx / 2, cy, 1, 0, 0, 1, sy), (cx - sx / 2, cy, -1, 0, 0, 1, sy)]


def _abox(mat, fx, fy, ax, ay, along_len, out_len, z0, h):
    """Box aligned with a facade: size along the facade / outward."""
    if ax:
        box(mat, fx, fy, z0, along_len, out_len, h)
    else:
        box(mat, fx, fy, z0, out_len, along_len, h)


def crenellate(cx, cy, sx, sy, z, mat="stone"):
    for fx, fy, nx, ny, ax, ay, L in _faces(cx, cy, sx, sy):
        k = int(L / 1.6)
        for i in range(k):
            t = -L / 2 + (i + 0.5) * L / k
            _abox(mat, fx + ax * t - nx * 0.25, fy + ay * t - ny * 0.25, ax, ay, 0.8, 0.5, z, 0.9)


def pavilion(cx, cy, sx, sy, h, style, accent, front, big=False, steps=True):
    P = 0.45
    box("plot", cx, cy, -0.05, sx + 2.4, sy + 2.4, P + 0.05, tile=(5.12, 5.12))
    mat = {"glass": "glass", "najdi": "stone", "screen": "screen", "panel": "panel"}[style]
    box(mat, cx, cy, P, sx, sy, h, tile=FACADE_TILE[mat])
    top = P + h
    nfx, nfy = FRONT[front]
    face = [f for f in _faces(cx, cy, sx, sy) if (f[2], f[3]) == (nfx, nfy)][0]
    fx, fy, nx, ny, ax, ay, L = face
    coll_h = top + 0.5
    # front steps
    if steps:
        _abox("plot", fx + nx * 1.6, fy + ny * 1.6, ax, ay, min(10, L * 0.6), 1.2, -0.05, 0.25)

    if style == "najdi":
        rect("roof", cx - sx / 2, cy - sy / 2, cx + sx / 2, cy + sy / 2, top + 0.01)
        crenellate(cx, cy, sx, sy, top)
        box(accent, cx, cy, top - 1.4, sx + 0.12, sy + 0.12, 0.3)
        for sxn in (-1, 1):
            for syn in (-1, 1):
                box("stone", cx + sxn * sx / 2, cy + syn * sy / 2, P, 1.8, 1.8, h + 1.4, tile=(4, 4))
        _abox("wood", fx + nx * 0.12, fy + ny * 0.12, ax, ay, 2.8, 0.3, P, 3.4)
        tiers = [(0.6, 3.4)] + ([(0.32, 2.4)] if big else [])
        z, tcx, tcy = top, cx - nfx * sx * 0.08, cy - nfy * sy * 0.08
        for k, th in tiers:
            tsx, tsy = sx * k, sy * k
            box("stone", tcx, tcy, z, tsx, tsy, th, tile=(4, 4))
            z += th
            rect("roof", tcx - tsx / 2, tcy - tsy / 2, tcx + tsx / 2, tcy + tsy / 2, z + 0.01)
            crenellate(tcx, tcy, tsx, tsy, z)
            S.coll.append((tcx, tcy, 0, tsx, tsy, z + 0.9, 0.0))
        coll_h = top + 0.9
    else:
        ov = 1.2
        box("white", cx, cy, top, sx + 2 * ov, sy + 2 * ov, 0.45)
        rect("roof", cx - sx / 2 - ov + 0.3, cy - sy / 2 - ov + 0.3, cx + sx / 2 + ov - 0.3,
             cy + sy / 2 + ov - 0.3, top + 0.46)
        _abox(accent, fx + nx * (ov + 0.07), fy + ny * (ov + 0.07), ax, ay, L + 2 * ov, 0.14, top + 0.05, 0.35)
        for k in (-0.25, 0.2):
            box("kerb", cx + k * sx, cy - k * sy, top + 0.45, 2.2, 1.6, 1.0)
        # entrance canopy
        cl = min(8.0, L * 0.5)
        _abox("white", fx + nx * 1.6, fy + ny * 1.6, ax, ay, cl, 3.2, P + 3.6, 0.3)
        for s in (-1, 1):
            cyl("white", fx + nx * 3.0 + ax * s * (cl / 2 - 0.3), fy + ny * 3.0 + ay * s * (cl / 2 - 0.3),
                P, 3.6, 0.14, seg=8, top=False)
        # accent blade
        t = L * 0.32
        _abox(accent, fx + nx * 0.3 + ax * t, fy + ny * 0.3 + ay * t, ax, ay, 1.0, 0.6, P, h + 1.6)
        if style == "glass":
            for ffx, ffy, fnx, fny, fax, fay, FL in _faces(cx, cy, sx, sy):
                k = int(FL / 3)
                for i in range(1, k):
                    t = -FL / 2 + i * FL / k
                    _abox("white", ffx + fnx * 0.25 + fax * t, ffy + fny * 0.25 + fay * t, fax, fay,
                          0.18, 0.5, P, h)
        if style == "screen":
            for sxn in (-1, 1):
                for syn in (-1, 1):
                    box("white", cx + sxn * sx / 2, cy + syn * sy / 2, P, 0.8, 0.8, h)
    S.coll.append((cx, cy, 0, sx + 2.4, sy + 2.4, coll_h, 0.0))


def innovation_dome(cx, cy, r=12.0):
    cyl("plot", cx, cy, -0.05, 1.0, r + 1.2, seg=40, T=5.12)
    dome("lattice", cx, cy, 0.95, r, 0.78)
    cyl("teal", cx, cy, 0.95, 0.5, r + 0.12, seg=40, top=False)
    cyl("white", cx, cy, 0.95 + r * 0.78 - 0.35, 0.7, 1.8, seg=16)
    box("white", cx, cy - r + 0.8, 0.95, 7.0, 5.0, 5.2)
    box("glass", cx, cy - r - 1.75, 0.95, 4.6, 0.2, 4.0, tile=(3, 4))
    box("teal", cx, cy - r + 0.8, 6.15, 7.1, 5.1, 0.35)
    S.coll.append((cx, cy, 0, 2 * r * 0.75, 2 * r * 0.75, 0.95 + r * 0.78 + 0.4, 0.0))
    S.coll.append((cx, cy - r + 0.8, 0, 7.0, 5.0, 6.2, 0.0))


def amphitheatre(cx, cy, face_deg=135.0, r_in=3.5, step=1.1, n=5):
    f = math.radians(face_deg)
    a0, a1 = f + math.pi / 2, f + 3 * math.pi / 2
    for i in range(n):
        r0 = r_in + i * step
        arc_prism("terracotta" if i % 2 else "kerb", cx, cy, r0, r0 + step, a0, a1, -0.05, (i + 1) * 0.45, seg=22)
    rl = r_in + n * step
    arc_prism("stone", cx, cy, rl, rl + 0.4, a0, a1, -0.05, n * 0.45 + 1.0, seg=22)
    arc_prism("wood", cx, cy, 0.0, r_in - 0.3, f - math.pi / 2, f + math.pi / 2, -0.05, 0.6, seg=14)
    sx, sy = cx + math.cos(f) * 1.2, cy + math.sin(f) * 1.2
    box("white", sx, sy, 5.0, 7.5, 4.5, 0.25, yaw=f + math.pi / 2)
    for s in (-1, 1):
        px = sx + math.cos(f + math.pi / 2) * s * 3.4 + math.cos(f) * 1.6
        py = sy + math.sin(f + math.pi / 2) * s * 3.4 + math.sin(f) * 1.6
        cyl("metal", px, py, 0, 5.0, 0.12, seg=8, top=False)
    S.coll.append((cx, cy, 0, 2 * rl, 2 * rl, 3.3, 0.0))


def first_aid_center():
    cx, cy = FA_C
    sx, sy = FA_SIZE
    pavilion(cx, cy, sx, sy, FA_H, "panel", "green", "W", steps=False)
    top = 0.45 + FA_H + 0.46
    box("white", cx, cy, top, 7.0, 7.0, 0.04)
    box("red", cx, cy, top + 0.04, 5.0, 1.5, 0.04)
    box("red", cx, cy, top + 0.04, 1.5, 5.0, 0.04)
    fx = cx - sx / 2
    box("metal", fx - 0.06, FA_DOOR[1], 0.0, 0.14, 2.8, 3.0)          # open doorway
    box("green", fx - 0.08, cy, 0.45 + FA_H - 1.3, 0.12, sy + 0.2, 0.5)  # green band
    for s in (-1, 1):                                                    # sign cubes
        box("white", fx - 0.6, FA_DOOR[1] + s * 2.1, 0.0, 0.25, 0.25, 2.4)
        box("medsign", fx - 0.6, FA_DOOR[1] + s * 2.1, 2.4, 0.8, 0.8, 0.8, unit=True)
        S.props.append((fx - 0.6, FA_DOOR[1] + s * 2.1, 0.5))


# =============================================================================
# Props
# =============================================================================
MATS.update({
    "canopy": dict(color=(0.97, 0.97, 0.95), rough=0.6),
    "skin1": dict(color=(0.80, 0.62, 0.48)), "skin2": dict(color=(0.62, 0.45, 0.33)),
    "skin3": dict(color=(0.45, 0.31, 0.22)), "hair": dict(color=(0.09, 0.07, 0.06)),
    "abaya": dict(color=(0.05, 0.05, 0.06), rough=0.7), "thobe": dict(color=(0.95, 0.95, 0.93)),
    "shemagh": dict(color=(0.78, 0.12, 0.14)), "denim": dict(color=(0.17, 0.22, 0.34)),
    "khaki": dict(color=(0.55, 0.50, 0.40)), "navy": dict(color=(0.10, 0.16, 0.32)),
    "sky": dict(color=(0.42, 0.62, 0.82)),
})
SHIRTS = ["teal", "gold", "terracotta", "green", "red", "pink", "yellow", "white", "navy", "sky", "orange"]
SKINS = ["skin1", "skin1", "skin2", "skin2", "skin3"]


def _ico0():
    t = (1 + 5 ** 0.5) / 2
    v = [_unit(p) for p in [(-1, t, 0), (1, t, 0), (-1, -t, 0), (1, -t, 0), (0, -1, t), (0, 1, t),
                            (0, -1, -t), (0, 1, -t), (t, 0, -1), (t, 0, 1), (-t, 0, -1), (-t, 0, 1)]]
    f = [(0, 11, 5), (0, 5, 1), (0, 1, 7), (0, 7, 10), (0, 10, 11), (1, 5, 9), (5, 11, 4), (11, 10, 2),
         (10, 7, 6), (7, 1, 8), (3, 9, 4), (3, 4, 2), (3, 2, 6), (3, 6, 8), (3, 8, 9), (4, 9, 5),
         (2, 4, 11), (6, 2, 10), (8, 6, 7), (9, 8, 1)]
    return [(v[a], v[b], v[c]) for a, b, c in f]


ICO0 = _ico0()


def blob0(mat, c, r, kz=1.0):
    """20-triangle ball (heads, palm crowns)."""
    m = S.m(mat)
    for tri in ICO0:
        pts = [(c[0] + p[0] * r, c[1] + p[1] * r, c[2] + p[2] * r * kz) for p in tri]
        n = tuple(sum(p[i] for p in tri) for i in range(3))
        m.poly(pts, [(0, 0), (1, 0), (0, 1)], (n[0], n[1], n[2] / max(kz, 0.1)))


def palm(x, y, h, rng):
    S.palms += 1
    cyl("palm_trunk", x, y, -0.05, h, 0.24, 0.17, seg=6, top=False)
    cyl("palm_trunk", x, y, h * 0.55, 0.14, 0.24, seg=6, top=False)
    blob0("palm_trunk", (x, y, h + 0.05), 0.35, 0.9)
    nf = 8
    for i in range(nf):
        az = i * 2 * math.pi / nf + rng.uniform(-0.2, 0.2)
        p = (x, y, h + 0.1)
        spec = [(0.9, 1.0, 0.45), (0.5, 0.9, 0.35)] if i % 3 == 0 else [(0.35, 1.5, 0.6), (-0.45, 1.4, 0.45)]
        for el, L, w in spec:
            el += rng.uniform(-0.1, 0.1)
            q = (p[0] + math.cos(el) * math.cos(az) * L, p[1] + math.cos(el) * math.sin(az) * L,
                 p[2] + math.sin(el) * L)
            seg_box("frond", p, q, w, 0.05)
            p = q


def canopy(cx, cy, sx, sy, h=4.4, accent="teal"):
    """White shade canopy on poles (typical expo / Riyadh Season shade structure)."""
    nx, ny = max(2, int(sx / 7) + 1), max(2, int(sy / 7) + 1)
    for i in range(nx):
        for j in range(ny):
            px = cx - sx / 2 + 0.4 + i * (sx - 0.8) / (nx - 1)
            py = cy - sy / 2 + 0.4 + j * (sy - 0.8) / (ny - 1)
            cyl("metal", px, py, -0.05, h, 0.11, seg=6, top=False)
            S.props.append((px, py, 0.35))
    box("canopy", cx, cy, h, sx, sy, 0.12)
    box("canopy", cx, cy, h + 0.12, sx * 0.7, sy * 0.7, 0.35)
    for s in (-1, 1):
        box(accent, cx, cy + s * (sy / 2 + 0.03), h - 0.05, sx, 0.06, 0.3)
    S.hot.append((cx, cy, min(sx, sy) * 0.45, 2.0))


def kiosk(x, y, yaw, accent):
    """Food / drinks kiosk with an awning on its front (local +x)."""
    c, s = math.cos(yaw), math.sin(yaw)
    box("white", x, y, -0.05, 2.4, 3.0, 2.75, yaw=yaw)
    box(accent, x, y, 2.7, 2.5, 3.1, 0.25, yaw=yaw)
    box(accent, x + c * 1.6, y + s * 1.6, 2.15, 0.9, 3.1, 0.08, yaw=yaw)
    box("wood", x + c * 1.25, y + s * 1.25, 0.95, 0.15, 2.6, 0.1, yaw=yaw)
    S.props.append((x, y, 2.0))
    S.hot.append((x + c * 3.2, y + s * 3.2, 2.2, 1.5))


def lowpoly_person(x, y, yaw, rng):
    """~70-90 triangle visitor baked into the scenery (never moves)."""
    sc = rng.uniform(0.93, 1.06) if rng.random() > 0.08 else rng.uniform(0.6, 0.72)   # some children
    c, s = math.cos(yaw), math.sin(yaw)

    def P(lx, ly):
        return x + c * lx - s * ly, y + s * lx + c * ly

    def part(mat, lx, ly, z0, dx, dy, dz):
        px, py = P(lx * sc, ly * sc)
        box(mat, px, py, z0 * sc, dx * sc, dy * sc, dz * sc, yaw=yaw)

    u = rng.random()
    skin = rng.choice(SKINS)
    if u < 0.30:                                       # thobe + shemagh
        part("thobe", 0, 0, -0.04, 0.27, 0.40, 1.46)
        for k in (-1, 1):
            part("thobe", 0.02, k * 0.255, 0.80, 0.11, 0.11, 0.58)
        blob0(skin, (x, y, 1.53 * sc), 0.115 * sc, 1.12)
        hd = "shemagh" if rng.random() < 0.6 else "thobe"
        part(hd, 0, 0, 1.56, 0.27, 0.27, 0.10)
        part(hd, -0.12, 0, 1.36, 0.05, 0.28, 0.28)
    elif u < 0.55:                                     # abaya
        part("abaya", 0, 0, -0.04, 0.30, 0.42, 1.44)
        for k in (-1, 1):
            part("abaya", 0.02, k * 0.26, 0.80, 0.12, 0.12, 0.56)
        blob0("abaya", (x, y, 1.53 * sc), 0.125 * sc, 1.12)
    else:                                              # casual
        pants = rng.choice(["denim", "denim", "khaki", "hair"])
        shirt = rng.choice(SHIRTS)
        for k in (-1, 1):
            part(pants, 0, k * 0.1, -0.04, 0.15, 0.14, 0.86)
            part(shirt, 0.01, k * 0.255, 1.02, 0.11, 0.11, 0.36)
            part(skin, 0.02, k * 0.255, 0.78, 0.09, 0.09, 0.26)
        part(shirt, 0, 0, 0.80, 0.24, 0.42, 0.60)
        blob0(skin, (x, y, 1.52 * sc), 0.115 * sc, 1.12)
        part("hair", -0.01, 0, 1.58, 0.21, 0.21, 0.07)
    S.crowd += 1


def tree(x, y, rng, s=1.0):
    cyl("trunk", x, y, -0.05, 2.4 * s, 0.18 * s, 0.14 * s, seg=6, top=False)
    mat = "leaf_a" if rng.random() < 0.5 else "leaf_b"
    blob(mat, (x, y, 3.5 * s), 2.0 * s, 0.85)
    blob(mat, (x + 0.8 * s, y + 0.4 * s, 4.5 * s), 1.4 * s, 0.85)
    blob(mat, (x - 0.7 * s, y - 0.5 * s, 4.1 * s), 1.3 * s, 0.85)


def shrub(x, y, r=0.7):
    blob("shrub", (x, y, 0.35), r, 0.7)


def lamp(x, y, dx, dy, k):
    cyl("metal", x, y, -0.05, 6.5, 0.1, 0.07, seg=8, top=False)
    seg_box("metal", (x, y, 6.35), (x + dx * 1.1, y + dy * 1.1, 6.35), 0.08, 0.08)
    box("white", x + dx * 1.2, y + dy * 1.2, 6.15, 0.55, 0.55, 0.18)
    ax, ay = -dy, dx
    bx, by = x + ax * 0.42, y + ay * 0.42
    if ax:
        box(BANNERS[k % 4], bx, by, 3.2, 0.7, 0.05, 1.9)
    else:
        box(BANNERS[k % 4], bx, by, 3.2, 0.05, 0.7, 1.9)


def bench(x, y, yaw):
    box("wood", x, y, 0.42, 1.8, 0.5, 0.08, yaw=yaw)
    c, s = math.cos(yaw), math.sin(yaw)
    box("wood", x + s * 0.22, y - c * 0.22, 0.55, 1.8, 0.06, 0.45, yaw=yaw)
    for k in (-0.7, 0.7):
        box("metal", x + c * k, y + s * k, -0.05, 0.08, 0.45, 0.48, yaw=yaw)


def med_point(x, y):
    box("white", x, y, -0.05, 0.5, 0.5, 2.3)
    box("medsign", x, y, 2.25, 0.95, 0.95, 0.95, unit=True)


def flag(x, y, mat, h=7.0):
    cyl("white", x, y, -0.05, h, 0.07, 0.05, seg=6, top=False)
    box(mat, x + 0.8, y, h - 1.1, 1.5, 0.04, 1.0)


def umbrella(x, y, mat):
    cyl("metal", x, y, -0.05, 2.4, 0.04, seg=6, top=False)
    cone(mat, x, y, 2.05, 0.55, 1.5, seg=10)
    cyl("white", x, y, -0.05, 0.75, 0.45, seg=10)


def fountain(cx, cy):
    cyl("kerb", cx, cy, -0.05, 0.6, 3.4, seg=28, top=False)
    cyl("kerb", cx, cy, -0.05, 0.6, 3.05, seg=28, top=False, inward=True)
    annulus("kerb", cx, cy, 3.05, 3.4, 0.55, seg=28)
    disk("water", cx, cy, 3.05, 0.38, seg=28, T=3.0)
    cyl("white", cx, cy, 0.38, 1.3, 0.35, seg=10, top=False)
    cyl("white", cx, cy, 1.6, 0.25, 0.9, 1.15, seg=16, top=False)
    disk("water", cx, cy, 1.05, 1.82, seg=16, T=3.0)
    cyl("white", cx, cy, 1.82, 0.6, 0.12, 0.05, seg=8)


def scatter(rng, pts_fn, n, kind, r, street_m=7.0, tries=400):
    placed = 0
    for _ in range(tries):
        if placed >= n:
            break
        p = pts_fn(rng)
        if p and try_place(p, r, street_m=street_m):
            if kind == "tree":
                tree(*p, rng, rng.uniform(0.85, 1.15))
            elif kind == "shrub":
                shrub(*p, rng.uniform(0.5, 0.9))
            elif kind == "palm":
                palm(*p, rng.uniform(6.0, 7.5), rng)
            placed += 1


def in_rect(r):
    return lambda g: (g.uniform(r[0], r[2]), g.uniform(r[1], r[3]))


def in_poly(poly):
    xs, ys = [p[0] for p in poly], [p[1] for p in poly]

    def inside(x, y):
        c = False
        for (x0, y0), (x1, y1) in zip(poly, poly[1:] + poly[:1]):
            if (y0 > y) != (y1 > y) and x < x0 + (y - y0) * (x1 - x0) / (y1 - y0):
                c = not c
        return c

    def f(g):
        for _ in range(50):
            x, y = g.uniform(min(xs), max(xs)), g.uniform(min(ys), max(ys))
            if inside(x, y):
                return x, y
        return None
    return f


# Pavilions: (cx, cy, sx, sy, h, style, accent, front)
PAVILIONS = [
    (-68.0, 30.0, 24, 18, 8.0, "najdi", "gold", "E"),        # West: Nations
    (-68.0, 80.0, 24, 22, 9.0, "glass", "teal", "E"),        # West: Innovation
    (68.0, 30.0, 24, 18, 8.0, "screen", "terracotta", "W"),  # East: Culture
    (68.0, 80.0, 24, 22, 9.0, "panel", "green", "W"),        # East: Sustainability
]
SAUDI = (-62.0, 132.0, 30, 16, 8.0, "najdi", "green", "S")
DOME = (0.0, 140.0, 10.0)


def _front_point(cx, cy, sx, sy, front, d):
    nx, ny = FRONT[front]
    return cx + nx * (sx / 2 + d), cy + ny * (sy / 2 + d)


def buildings():
    for p in PAVILIONS:
        pavilion(*p)
        fx, fy = _front_point(p[0], p[1], p[2], p[3], p[7], 5.0)
        S.hot.append((fx, fy, 5.0, 3.0))
    pavilion(*SAUDI, big=True)
    S.hot.append((*_front_point(SAUDI[0], SAUDI[1], SAUDI[2], SAUDI[3], "S", 5.0), 6.0, 3.0))
    innovation_dome(*DOME)
    S.hot.append((DOME[0] + 9.0, DOME[1] - DOME[2] - 3.5, 3.5, 1.5))
    S.hot.append((DOME[0] - 9.0, DOME[1] - DOME[2] - 3.5, 3.5, 1.5))
    amphitheatre(-58.0, -6.0, face_deg=45.0)
    S.hot.append((-52.0, 0.0, 4.0, 1.5))
    first_aid_center()
    perimeter()


def inner_zones(rng, lite):
    dens = 0.5 if lite else 1.0
    for side in (-1, 1):
        poly = inner_poly(side)
        gpoly("grass", poly, Z_GRASS)
        # paved rest area under a shade canopy, fountain near the start, kiosks near the top
        cx = side * 18.0
        rect("plot", cx - 7, 50, cx + 7, 78, Z_GRASS + 0.002, T=5.12)
        canopy(cx, 64.0, 12.0, 24.0, accent="teal" if side < 0 else "gold")
        for j in range(4):
            bench(cx - 3.0, 55.0 + j * 6.0, math.pi / 2)
            bench(cx + 3.0, 55.0 + j * 6.0, -math.pi / 2)
        disk("plot", cx, FOUNTAIN_Y, 5.5, Z_GRASS + 0.002, seg=32, T=5.12)
        fountain(cx, FOUNTAIN_Y)
        S.props.append((cx, FOUNTAIN_Y, 3.6))
        S.hot.append((cx, FOUNTAIN_Y, 5.2, 2.0))
        rect("plot", cx - 6, 84, cx + 6, 94, Z_GRASS + 0.002, T=5.12)
        for j, acc in enumerate(BANNERS[:2]):
            kiosk(cx - 3.5 + j * 7.0, 91.5, -math.pi / 2, acc)
        for j in range(3):
            if try_place((cx - 4.0 + j * 4.0, 86.0), 1.5, street_m=6.0):
                umbrella(cx - 4.0 + j * 4.0, 86.0, BANNERS[(j + 2) % 4])
        scatter(rng, in_poly(poly), int(9 * dens), "tree", 2.2)
        scatter(rng, in_poly(poly), int(8 * dens), "shrub", 0.8)


def outer_zones(rng, lite):
    dens = 0.5 if lite else 1.0
    # gardens between the side pavilions
    for side in (-1, 1):
        r = (min(side * 48, side * 90), 44.0, max(side * 48, side * 90), 64.0)
        rect("grass", *r, Z_GRASS)
        scatter(rng, in_rect(r), int(8 * dens), "tree", 2.2)
        scatter(rng, in_rect(r), int(6 * dens), "shrub", 0.8)
        for j in range(3):
            if try_place((side * 69.0, 47.5 + j * 6.5), 1.0):
                bench(side * 69.0, 47.5 + j * 6.5, 0.0)
    # Family park with a lagoon (north-east)
    lx, ly, rx, ry = 62.0, 128.0, 14.0, 8.5
    rect("grass", 40.0, 110.0, 92.0, 153.0, Z_GRASS)
    disk("water", lx, ly, rx, -0.02, seg=40, T=6.0, ky=ry / rx)
    annulus("kerb", lx, ly, rx, rx + 0.6, 0.22, seg=40, ky=ry / rx)
    cyl("kerb", lx, ly, -0.05, 0.27, rx + 0.6, seg=40, top=False, ky=ry / rx)
    S.water.append((lx, ly, rx + 0.6, ry + 0.6))
    box("wood", lx, ly, 0.3, 2 * rx + 3, 2.4, 0.25)
    for s in (-1, 1):
        box("white", lx, ly + s * 1.15, 1.2, 2 * rx + 3, 0.08, 0.08)
    for i in range(16):
        a = 2 * math.pi * i / 16
        p = (lx + (rx + 4.0) * math.cos(a), ly + (ry + 4.0) * math.sin(a))
        if try_place(p, 1.8):
            tree(*p, rng, rng.uniform(0.9, 1.1))
    S.hot.append((lx, ly - ry - 3.0, 4.0, 2.0))
    S.hot.append((lx - rx - 3.0, ly, 3.0, 1.0))
    # food court (south-east)
    for i, (kx, ky) in enumerate(((44.0, -6.0), (56.0, -6.0), (68.0, -6.0), (80.0, -6.0))):
        kiosk(kx, ky, -math.pi / 2, BANNERS[i])
    for i in range(10):
        p = (44.0 + (i % 5) * 9.0, -16.0 - (i // 5) * 6.0)
        if try_place(p, 1.6, street_m=6.0):
            umbrella(*p, BANNERS[i % 4])
    S.hot.append((62.0, -15.0, 12.0, 4.0))
    # flags on the west side of the start plaza (out of the scan view)
    for i in range(7):
        a = math.radians(160 + i * 12)
        p = (START[0] + (START_R + 3.0) * math.cos(a), START[1] + (START_R + 3.0) * math.sin(a))
        if try_place(p, 0.5, street_m=5.0, plaza_ok=True):
            flag(*p, ["green", "white", "gold", "teal"][i % 4])
    # palms around the Incident Plaza
    for i in range(20):
        a = math.radians(i * 18 + 9)
        p = (INCIDENT[0] + (INCIDENT_R + 2.0) * math.cos(a), INCIDENT[1] + (INCIDENT_R + 2.0) * math.sin(a))
        if try_place(p, 1.4, street_m=6.0, plaza_ok=True):
            disk("grass", *p, 1.0, Z_WALK + 0.004, seg=12)
            palm(*p, rng.uniform(6.2, 7.2), rng)
    for i in range(10):
        a = math.radians(i * 36 + 18)
        p = (INCIDENT[0] + 11.5 * math.cos(a), INCIDENT[1] + 11.5 * math.sin(a))
        if try_place(p, 1.0, street_m=0.0, plaza_ok=True):
            bench(*p, a + math.pi / 2)
    S.hot.append((INCIDENT[0], INCIDENT[1] + 9.0, 4.0, 2.0))


def streetscape(rng, lite, palms=True):
    spacing = 32.0 if lite else 16.0
    k = 0
    for a, b in ROAD_SEGS:
        L = math.hypot(b[0] - a[0], b[1] - a[1])
        ux, uy = (b[0] - a[0]) / L, (b[1] - a[1]) / L
        t = 6.0
        while t < L - 3:
            for side in ((1, -1) if palms else ()):
                px, py = a[0] + ux * t - uy * PALM_OFF * side, a[1] + uy * t + ux * PALM_OFF * side
                if try_place((px, py), 1.4, street_m=PALM_OFF - 0.3):
                    disk("grass", px, py, 1.0, Z_WALK + 0.004, seg=12)
                    palm(px, py, rng.uniform(6.2, 7.4), rng)
            tl = t + spacing / 2
            for side in (1, -1):
                lx, ly = a[0] + ux * tl - uy * PALM_OFF * side, a[1] + uy * tl + ux * PALM_OFF * side
                if try_place((lx, ly), 0.5, street_m=PALM_OFF - 0.3):
                    lamp(lx, ly, uy * side, -ux * side, k)
                    k += 1
            t += spacing


# =============================================================================
# Ambient crowd (baked low-poly visitors)
# =============================================================================
class _Grid:
    def __init__(self, cell=1.0):
        self.cell, self.d = cell, {}

    def near(self, p, r):
        c = self.cell
        i0, j0 = int(math.floor(p[0] / c)), int(math.floor(p[1] / c))
        k = int(math.ceil(r / c))
        for i in range(i0 - k, i0 + k + 1):
            for j in range(j0 - k, j0 + k + 1):
                for q in self.d.get((i, j), ()):
                    if math.hypot(p[0] - q[0], p[1] - q[1]) < r:
                        return True
        return False

    def add(self, p):
        key = (int(math.floor(p[0] / self.cell)), int(math.floor(p[1] / self.cell)))
        self.d.setdefault(key, []).append(p)


def _shape_keep(p, rng, bearings):
    """Near the start, keep the ambient crowd in the same order as the route crowds,
    so it never changes which route the drone picks."""
    hx, hy = HOVER_POINT
    d = math.hypot(p[0] - hx, p[1] - hy)
    if d > SHAPE_M or p[1] < hy - 2.0:
        return True
    b = math.degrees(math.atan2(p[1] - hy, p[0] - hx))
    name = min(bearings, key=lambda n: abs((b - bearings[n] + 180) % 360 - 180))
    return rng.random() < SHAPE[name]


def ambient_ok(p, grid, big_props, small_props):
    x, y = p
    if not (SITE[0] + 2 < x < SITE[2] - 2 and SITE[1] + 2 < y < SITE[3] - 2):
        return False
    if street_dist(p) < ROAD_W / 2 + 0.6:
        return False
    if math.hypot(x - START[0], y - START[1]) < START_R + 1.0:
        return False
    if math.hypot(x - INCIDENT[0], y - INCIDENT[1]) < 7.5:
        return False
    if in_corridor(p, 0.6) or in_coll(p, 0.4) or in_water(p, 0.4) or near_walk(p, 0.75):
        return False
    for qx, qy, qr in big_props:
        if math.hypot(x - qx, y - qy) < qr * 0.95:
            return False
    for qx, qy, qr in small_props:
        if math.hypot(x - qx, y - qy) < 0.55:
            return False
    if any(math.hypot(x - qx, y - qy) < 1.0 for qx, qy in S.reserved):
        return False
    return not grid.near(p, 0.68)


def ambient_crowd(rng, n):
    bearings = scan_bearings()
    big = [q for q in S.props if q[2] >= 1.9 and q[2] < 5.0]
    small = [q for q in S.props if q[2] < 1.9]
    grid = _Grid(1.0)
    placed = []
    tries = 0
    while len(placed) < n and tries < n * 60:
        tries += 1
        if rng.random() < 0.93:                     # spectators at the kerb of the avenues
            a, b = rng.choice(ROAD_SEGS)
            L = math.hypot(b[0] - a[0], b[1] - a[1])
            t, side = rng.uniform(0, L), rng.choice((-1, 1))
            ux, uy = (b[0] - a[0]) / L, (b[1] - a[1]) / L
            off = rng.uniform(ROAD_W / 2 + 0.5, ROAD_W / 2 + 2.3) * side
            c = (a[0] + ux * t - uy * off, a[1] + uy * t + ux * off)
            face = (a[0] + ux * t, a[1] + uy * t)    # watching the avenue
        else:                                       # round the edge of the Incident Plaza
            ang, r = rng.uniform(0, 2 * math.pi), rng.uniform(9.0, INCIDENT_R + 2.5)
            c = (INCIDENT[0] + r * math.cos(ang), INCIDENT[1] + r * math.sin(ang))
            face = INCIDENT
        if not _shape_keep(c, rng, bearings):
            continue
        size = min(rng.choice((1, 2, 2, 3, 3, 4, 5)), n - len(placed))
        group = []
        for _ in range(size * 3):
            if len(group) >= size:
                break
            p = c if not group else (c[0] + rng.uniform(-1.3, 1.3), c[1] + rng.uniform(-1.3, 1.3))
            if ambient_ok(p, grid, big, small):
                grid.add(p)
                group.append(p)
        gx = sum(q[0] for q in group) / max(1, len(group))
        gy = sum(q[1] for q in group) / max(1, len(group))
        for p in group:
            if len(group) > 1 and rng.random() < 0.6:
                yaw = math.atan2(gy - p[1], gx - p[0])
            elif face is not None:
                yaw = math.atan2(face[1] - p[1], face[0] - p[0])
            else:
                yaw = rng.uniform(-math.pi, math.pi)
            yaw += rng.uniform(-0.35, 0.35)
            lowpoly_person(p[0], p[1], yaw, rng)
            placed.append((round(p[0], 2), round(p[1], 2)))
    if len(placed) < n:
        print(f"NOTE: placed {len(placed)}/{n} ambient visitors (site is full)")
    return placed


# =============================================================================
# Walkers (moving visitors: one mesh + one texture each, moved by sim_node.py)
# =============================================================================
ATLAS_COLS = 32
ATLAS_NAMES = ["thobe", "shemagh", "abaya", "hair", "denim", "khaki", "skin1", "skin2", "skin3"] + SHIRTS
N_VARIANTS = 16


def _shape_factor(p):
    hx, hy = HOVER_POINT
    if math.hypot(p[0] - hx, p[1] - hy) > SHAPE_M or p[1] < hy - 2.0:
        return 1.0
    bearings = scan_bearings()
    b = math.degrees(math.atan2(p[1] - hy, p[0] - hx))
    return SHAPE[min(bearings, key=lambda n: abs((b - bearings[n] + 180) % 360 - 180))]


def _seg_route(a, b):
    for name, r in ROUTES.items():
        line = _dedup(list(r["wp"]) + [INCIDENT])
        if (a, b) in list(zip(line, line[1:])):
            return name
    return None


def _walk_ok(p, own=None):
    x, y = p
    if not (SITE[0] + 3 < x < SITE[2] - 3 and SITE[1] + 3 < y < SITE[3] - 3):
        return False
    if in_plaza(p, 2.5) or in_corridor(p, 0.8) or in_coll(p, 0.9) or in_water(p, 1.0):
        return False
    if any(math.hypot(x - qx, y - qy) < 1.2 for qx, qy in S.reserved):   # route people + clear spots
        return False
    # stay off this avenue's carriageway and clear of every other avenue's sidewalk lanes
    return all(d_seg(p, a, b) > (ROAD_W / 2 + 1.2 if (a, b) == own else WALK_LANES[1] + 1.0)
               for a, b in ROAD_SEGS)


def _lane_loop(a, b, off_a, off_b, own=None, min_len=10.0):
    """Closed loop: along line a->b at offset off_a, back at offset off_b (longest valid run)."""
    L = math.hypot(b[0] - a[0], b[1] - a[1])
    ux, uy = (b[0] - a[0]) / L, (b[1] - a[1]) / L

    def pt(t, off):
        return (a[0] + ux * t - uy * off, a[1] + uy * t + ux * off)
    best, run = None, None
    n = int(L / 0.5)
    for k in range(n + 2):
        t = min(k * 0.5, L)
        ok = k <= n and _walk_ok(pt(t, off_a), own) and _walk_ok(pt(t, off_b), own)
        if ok and run is None:
            run = t
        if (not ok) and run is not None:
            t1 = max(run, t - 0.5)
            if t1 - run >= min_len and (best is None or t1 - run > best[1] - best[0]):
                best = (run, t1)
            run = None
    if not best:
        return None
    t0, t1 = best[0] + 0.5, best[1] - 0.5
    return [pt(t0, off_a), pt(t1, off_a), pt(t1, off_b), pt(t0, off_b)]


def walker_loops():
    loops = []
    for a, b in ROAD_SEGS:
        for side in (1, -1):
            lp = _lane_loop(a, b, side * WALK_LANES[0], side * WALK_LANES[1], own=(a, b))
            if lp:
                route = _seg_route(a, b)
                near = min(math.hypot(q[0] - HOVER_POINT[0], q[1] - HOVER_POINT[1]) for q in lp)
                f = SHAPE[route] if (route and near < SHAPE_M) else 1.0
                loops.append((lp, f))
    for lp, _ in loops:
        S.walk += list(zip(lp, lp[1:] + lp[:1]))
    return loops


def plan_walkers(rng, loops, lite):
    dens = WALK_DENS * (0.5 if lite else 1.0)
    out = []
    for lp, f in loops:
        L = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(lp, lp[1:] + lp[:1]))
        n = min(int(round(L * dens * f)), int(L / 2.5))
        if n <= 0:
            continue
        speed = round(rng.uniform(1.0, 1.45), 2)       # same speed per loop: nobody overlaps
        gap = L / n
        for i in range(n):
            out.append({"name": f"walker_{len(out):03d}", "loop": [[round(x, 2), round(y, 2)] for x, y in lp],
                         "speed": speed, "phase": round(i * gap + rng.uniform(0, 0.3 * gap), 2),
                         "variant": rng.randrange(N_VARIANTS)})
    return out


def _loop_pose(loop, s):
    pts = [tuple(p) for p in loop] + [tuple(loop[0])]
    L = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:]))
    s %= L
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        d = math.hypot(x1 - x0, y1 - y0)
        if s <= d and d > 1e-6:
            f = s / d
            return x0 + (x1 - x0) * f, y0 + (y1 - y0) * f, math.atan2(y1 - y0, x1 - x0)
        s -= d
    return pts[0][0], pts[0][1], 0.0


VARIANT_MESHES = []


def build_walker_assets():
    """16 one-mesh visitor variants sharing one small colour atlas (one draw call each)."""
    wdir = ASSETS / "walkers"
    wdir.mkdir(parents=True, exist_ok=True)
    n = 128
    cw = n // ATLAS_COLS
    cols = [MATS[nm]["color"] for nm in ATLAS_NAMES]
    _png(wdir / "atlas.png", n, lambda x, y: cols[min(x // cw, len(cols) - 1)])
    uv = {nm: ((i + 0.5) / ATLAS_COLS, 0.5) for i, nm in enumerate(ATLAS_NAMES)}
    files = []
    for i in range(N_VARIANTS):
        saved, saved_n = S.meshes, S.crowd
        S.meshes = {}
        lowpoly_person(0.0, 0.0, 0.0, random.Random(900 + i))
        parts, S.meshes, S.crowd = S.meshes, saved, saved_n
        m = Mesh()
        for nm, pm in parts.items():
            vb, nb = len(m.v), len(m.n)
            m.v.extend(pm.v)
            m.t.extend([uv[nm]] * len(pm.t))
            m.n.extend(pm.n)
            m.f.extend((i0 + vb, k, ni + nb) for i0, k, ni in pm.f)
        path = wdir / f"visitor_{i:02d}.obj"
        m.write(path)
        files.append(path)
        VARIANT_MESHES.append(m)
    return wdir / "atlas.png", files


def marker_sdf(x, y, z):
    """Bright halo + arrow that sim_node.py keeps floating above the drone (out of its camera view)."""
    mdir = ASSETS / "marker"
    mdir.mkdir(parents=True, exist_ok=True)
    saved = S.meshes
    S.meshes = {}
    seg = 32
    for i in range(seg):                                 # halo ring
        a0, a1 = 2 * math.pi * i / seg, 2 * math.pi * (i + 1) / seg
        seg_box("orange", (1.2 * math.cos(a0), 1.2 * math.sin(a0), 0.0),
                (1.2 * math.cos(a1), 1.2 * math.sin(a1), 0.0), 0.16, 0.12)
    cone("red", 0.0, 0.0, 0.25, -0.7, 0.45, seg=16)      # arrow head pointing down at the drone
    cyl("red", 0.0, 0.0, 0.25, 1.1, 0.12, seg=10)        # arrow shaft
    cyl("white", 0.0, 0.0, 1.35, 0.25, 0.3, seg=12)      # beacon cap
    parts, S.meshes = S.meshes, saved
    vis = ""
    emis = {"orange": "1.0 0.45 0.0 1", "red": "0.9 0.05 0.05 1", "white": "0.9 0.9 0.9 1"}
    for nm, m in parts.items():
        path = mdir / f"marker_{nm}.obj"
        m.write(path)
        c = MATS[nm]["color"]
        cs = f"{c[0]} {c[1]} {c[2]} 1"
        vis += (f"<visual name='{nm}'><cast_shadows>false</cast_shadows><geometry><mesh><uri>{path}</uri></mesh>"
                f"</geometry><material><ambient>{cs}</ambient><diffuse>{cs}</diffuse>"
                f"<emissive>{emis[nm]}</emissive></material></visual>")
    return (f"<model name='drone_marker'><static>true</static>{pose(x, y, z)}"
            f"<link name='link'>{vis}</link></model>\n")


def walkers_sdf(walkers, atlas, files):
    mat = (f"<material><ambient>1 1 1 1</ambient><diffuse>1 1 1 1</diffuse><specular>0.05 0.05 0.05 1</specular>"
           f"<pbr><metal><albedo_map>{atlas}</albedo_map><roughness>0.85</roughness>"
           f"<metalness>0.0</metalness></metal></pbr></material>")
    out = ""
    for w in walkers:
        x, y, h = _loop_pose(w["loop"], w["phase"])
        out += (f"<model name='{w['name']}'><static>true</static>{pose(x, y, 0, yw=h)}"
                f"<link name='link'><visual name='v'><geometry><mesh><uri>{files[w['variant']]}</uri>"
                f"</mesh></geometry>{mat}</visual></link></model>\n")
    return out


# =============================================================================
# People, golf cart (SDF primitives — moved at runtime)
# =============================================================================
def local_model_path(name):
    if not FUEL_CACHE.is_dir():
        return None
    for d in FUEL_CACHE.iterdir():
        if d.is_dir() and d.name.lower() == name.lower():
            versions = [v for v in d.iterdir()
                        if v.is_dir() and v.name.isdigit() and (v / "model.sdf").exists()]
            if versions:
                return max(versions, key=lambda v: int(v.name))
    return None


def resolve_people():
    found = {}
    LINKS.mkdir(parents=True, exist_ok=True)
    for name in PEOPLE:
        p = local_model_path(name)
        if p:
            link = LINKS / name.lower().replace(" ", "_")
            if link.is_symlink() or link.exists():
                link.unlink()
            link.symlink_to(p, target_is_directory=True)
            found[name] = link
            print(f"  model OK : {name} -> {link}")
        else:
            print(f"  MISSING  : {name} (not in {FUEL_CACHE}) - skipped")
    return found


def pose(x, y, z=0.0, r=0.0, p=0.0, yw=0.0):
    return f"<pose>{x:.3f} {y:.3f} {z:.3f} {r:.4f} {p:.4f} {yw:.4f}</pose>"


def _mat(rgb):
    c = f"{rgb[0]} {rgb[1]} {rgb[2]} 1"
    return (f"<material><ambient>{c}</ambient><diffuse>{c}</diffuse>"
            f"<specular>0.1 0.1 0.1 1</specular></material>")


def sbox(name, p, size, rgb, collision=False):
    geom = f"<geometry><box><size>{size[0]} {size[1]} {size[2]}</size></box></geometry>"
    s = f"<visual name='{name}_v'>{p}{geom}{_mat(rgb)}</visual>"
    if collision:
        s += f"<collision name='{name}_c'>{p}{geom}</collision>"
    return s


def scyl(name, p, radius, length, rgb):
    geom = (f"<geometry><cylinder><radius>{radius}</radius>"
            f"<length>{length}</length></cylinder></geometry>")
    return f"<visual name='{name}_v'>{p}{geom}{_mat(rgb)}</visual>"


def golf_cart():
    x, y, yaw = CART_POSE

    W, K = (0.95, 0.95, 0.95), (0.08, 0.08, 0.08)
    R, B = (0.85, 0.1, 0.1), (0.1, 0.2, 0.9)
    G = (0.35, 0.5, 0.6)

    b = sbox(
        "chassis",
        pose(0, 0, 0.5),
        (2.4, 1.2, 0.35),
        W,
        collision=True
    )

    b += sbox(
        "stripe_l",
        pose(0, 0.605, 0.5),
        (2.2, 0.01, 0.12),
        R
    )

    b += sbox(
        "stripe_r",
        pose(0, -0.605, 0.5),
        (2.2, 0.01, 0.12),
        R
    )

    b += sbox(
        "cross_lv",
        pose(0.3, 0.61, 0.85),
        (0.12, 0.01, 0.4),
        R
    )

    b += sbox(
        "cross_lh",
        pose(0.3, 0.61, 0.85),
        (0.4, 0.01, 0.12),
        R
    )

    b += sbox(
        "cross_rv",
        pose(0.3, -0.61, 0.85),
        (0.12, 0.01, 0.4),
        R
    )

    b += sbox(
        "cross_rh",
        pose(0.3, -0.61, 0.85),
        (0.4, 0.01, 0.12),
        R
    )

    b += sbox(
        "hood",
        pose(0.95, 0, 0.75),
        (0.5, 1.15, 0.2),
        W
    )

    b += sbox(
        "seat",
        pose(-0.2, 0, 0.85),
        (0.6, 1.1, 0.35),
        K
    )

    b += sbox(
        "backrest",
        pose(-0.5, 0, 1.15),
        (0.1, 1.1, 0.5),
        K
    )

    b += sbox(
        "windshield",
        pose(0.78, 0, 1.45, p=-0.25),
        (0.03, 1.1, 0.9),
        G
    )

    b += sbox(
        "roof",
        pose(-0.1, 0, 2.0),
        (2.0, 1.3, 0.06),
        W,
        collision=True
    )

    for i, (px, py) in enumerate([
        (0.75, 0.55),
        (0.75, -0.55),
        (-0.9, 0.55),
        (-0.9, -0.55)
    ]):
        b += sbox(
            f"post{i}",
            pose(px, py, 1.35),
            (0.05, 0.05, 1.25),
            K
        )

    b += sbox(
        "light_red",
        pose(0.8, 0.4, 2.09),
        (0.2, 0.3, 0.12),
        R
    )

    b += sbox(
        "light_blue",
        pose(0.8, -0.4, 2.09),
        (0.2, 0.3, 0.12),
        B
    )

    for i, (px, py) in enumerate([
        (0.8, 0.6),
        (0.8, -0.6),
        (-0.8, 0.6),
        (-0.8, -0.6)
    ]):
        b += scyl(
            f"wheel{i}",
            pose(px, py, 0.28, r=math.pi / 2),
            0.28,
            0.2,
            K
        )

    # Rear flatbed for stretcher + medics
    b += sbox(
        "bed_frame",
        pose(-2.3, 0, 0.45),
        (1.9, 1.0, 0.3),
        K
    )

    b += sbox(
        "bed",
        pose(-2.3, 0, 0.66),
        (2.1, 1.4, 0.12),
        W
    )

    for s, nm in ((1, "l"), (-1, "r")):
        b += sbox(
            f"bed_rail_{nm}",
            pose(-2.3, s * 0.69, 0.8),
            (2.1, 0.04, 0.16),
            K
        )

        b += sbox(
            f"bed_stripe_{nm}",
            pose(-2.3, s * 0.705, 0.62),
            (2.1, 0.01, 0.08),
            R
        )

        b += scyl(
            f"wheel_bed_{nm}",
            pose(-2.9, s * 0.6, 0.28, r=math.pi / 2),
            0.28,
            0.2,
            K
        )

    b += sbox(
        "bed_light",
        pose(-3.36, 0, 0.66),
        (0.02, 1.2, 0.08),
        R
    )

    return (
        f"<model name='golf_cart'>"

        # IMPORTANT: cart is movable
        f"<static>false</static>"

        f"{pose(x, y, 0, yw=yaw)}"

        f"<link name='link'>"

        # VelocityControl moves the model directly, so we don't need
        # physical wheel joints for this Expo prototype.
        f"<gravity>false</gravity>"

        f"<inertial>"
        f"<mass>300</mass>"
        f"<inertia>"
        f"<ixx>80</ixx>"
        f"<iyy>120</iyy>"
        f"<izz>150</izz>"
        f"<ixy>0</ixy>"
        f"<ixz>0</ixz>"
        f"<iyz>0</iyz>"
        f"</inertia>"
        f"</inertial>"

        f"{b}"
        f"</link>"

        # Receives velocity commands from ROS 2 through ros_gz_bridge
        f"<plugin "
        f"filename='gz-sim-velocity-control-system' "
        f"name='gz::sim::systems::VelocityControl'>"
        f"<topic>/golf_cart/cmd_vel</topic>"
        f"</plugin>"

        # Publishes the cart position back to ROS 2
        f"<plugin "
        f"filename='gz-sim-odometry-publisher-system' "
        f"name='gz::sim::systems::OdometryPublisher'>"
        f"<odom_topic>/golf_cart/odom</odom_topic>"
        f"<odom_frame>world</odom_frame>"
        f"<robot_base_frame>golf_cart</robot_base_frame>"
        f"<odom_publish_frequency>20</odom_publish_frequency>"
        f"<dimensions>2</dimensions>"
        f"</plugin>"

        f"</model>\n"
    )

def person(model_path, name, x, y, yaw):
    return (f"<include><uri>{model_path}</uri><name>{name}</name>"
            f"{pose(x, y, 0, yw=yaw)}<static>true</static></include>\n")


CARRY_Z, MEDIC_SPACING = 0.45, 1.35   # keep in sync with demo_common.py


def medic(model_path, name, x, y, yaw):
    """Person + hi-vis vest with red crosses (vest is a square box: works for any facing)."""
    V, Gy, R = (0.80, 0.95, 0.10), (0.75, 0.75, 0.78), (0.85, 0.08, 0.10)
    v = sbox("vest", pose(0, 0, 1.30), (0.46, 0.46, 0.52), V)
    for i, z in enumerate((1.17, 1.43)):
        v += sbox(f"stripe{i}", pose(0, 0, z), (0.47, 0.47, 0.05), Gy)
    v += sbox("cx1", pose(0, 0, 1.30), (0.475, 0.07, 0.22), R)
    v += sbox("cx2", pose(0, 0, 1.30), (0.475, 0.22, 0.07), R)
    v += sbox("cy1", pose(0, 0, 1.30), (0.07, 0.475, 0.22), R)
    v += sbox("cy2", pose(0, 0, 1.30), (0.22, 0.475, 0.07), R)
    return (f"<model name='{name}'><static>true</static>{pose(x, y, 0, yw=yaw)}"
            f"<include><uri>{model_path}</uri><name>{name}_body</name>{pose(0, 0, 0)}"
            f"<static>true</static></include><link name='vest'>{v}</link></model>\n")


def patient(model_path, x, y, yaw):
    """Person lying on their back, model origin at the body centre."""
    return (f"<model name='patient'><static>true</static>{pose(x, y, 0, yw=yaw)}"
            f"<include><uri>{model_path}</uri><name>patient_body</name>"
            f"{pose(0.88, 0, 0.16, p=-math.pi / 2)}<static>true</static></include></model>\n")


def stretcher(x, y, z, yaw):
    O, M, W = (0.95, 0.45, 0.10), (0.30, 0.30, 0.32), (0.95, 0.95, 0.95)
    b = sbox("deck", pose(0, 0, 0.33), (1.95, 0.55, 0.07), O)
    b += sbox("pillow", pose(-0.75, 0, 0.39), (0.3, 0.45, 0.06), W)
    for s, nm in ((1, "l"), (-1, "r")):
        b += sbox(f"rail_{nm}", pose(0, s * 0.29, 0.33), (2.35, 0.04, 0.04), M)
        for k, xx in enumerate((-0.8, 0.8)):
            b += sbox(f"leg_{nm}{k}", pose(xx, s * 0.25, 0.15), (0.04, 0.04, 0.3), M)
    return (f"<model name='stretcher'><static>true</static>{pose(x, y, z, yw=yaw)}"
            f"<link name='link'>{b}</link></model>\n")


def rescue_sdf(paths):
    """Medics at the First Aid Center door holding the stretcher, patient at the incident."""
    h = math.pi  # facing west, out of the door
    ux, uy = math.cos(h), math.sin(h)
    cx, cy = FA_DOOR
    out = medic(paths[0], "medic_0", cx + ux * MEDIC_SPACING, cy + uy * MEDIC_SPACING, h)
    out += medic(paths[1 % len(paths)], "medic_1", cx - ux * MEDIC_SPACING, cy - uy * MEDIC_SPACING, h)
    out += stretcher(cx, cy, CARRY_Z, h)
    out += patient(paths[0], *PATIENT)
    return out


def _clear_spot(x, y, h, side, own_line):
    off = ROAD_W / 2 + CLEAR_MARGIN
    c = (x - side * off * math.sin(h), y + side * off * math.cos(h))
    if in_plaza(c, 0.5) or in_coll(c, 0.5):
        return None
    if any(d_seg(c, a, b) < ROAD_W / 2 + 0.9 for a, b in ROAD_SEGS if (a, b) not in own_line):
        return None
    return c


USE_FUEL = [False]   # --fuel: detailed Fuel models in the scan zone (much heavier to render)


def plan_crowds(rng):
    """People standing ON each avenue, along its whole length. The first part
    (CROWD_RANGE, what the drone's scan sees up close) uses Fuel models; the rest uses
    the one-mesh visitors. Each has a roadside spot to hurry to when the drone comes."""
    placed, counts, meta = [], {}, []
    half = ROAD_W / 2 - 0.7
    for rname, r in ROUTES.items():
        n_scan, n_total = r["people"]
        line = _dedup(list(r["wp"]) + [INCIDENT])
        own = list(zip(line, line[1:]))
        L = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in own)
        s_end = L - INCIDENT_R - 1.0
        n, tries = 0, 0
        while n < n_total and tries < 20000:
            tries += 1
            scan = n < n_scan
            s = rng.uniform(*CROWD_RANGE) if scan else rng.uniform(CROWD_RANGE[1], s_end)
            x, y, h = along(line, s)
            off = rng.uniform(-half, half)
            px, py = x - off * math.sin(h), y + off * math.cos(h)
            if in_plaza((px, py), 0.5):
                continue
            if any(d_seg((px, py), a, b) < ROAD_W / 2 + 0.3 for a, b in ROAD_SEGS if (a, b) not in own):
                continue          # stay on this avenue only
            if any(math.hypot(px - qx, py - qy) < MIN_GAP for qx, qy in placed):
                continue
            side = 1.0 if off >= 0 else -1.0
            c = _clear_spot(x, y, h, side, own) or _clear_spot(x, y, h, -side, own)
            if c is None:
                continue
            placed.append((px, py))
            meta.append({
                "name": f"person_{rname}_{n:02d}", "route": rname, "s": round(s, 3),
                "x": round(px, 3), "y": round(py, 3), "yaw": round(rng.uniform(-math.pi, math.pi), 4),
                "clear_x": round(c[0], 3), "clear_y": round(c[1], 3), "fuel": scan and USE_FUEL[0],
            })
            n += 1
        counts[rname] = n
        if n < n_total:
            print(f"WARNING: only placed {n}/{n_total} on {rname} (avenue full)")
    # onlookers in closed rings round the injured visitor - right in the cart's way
    n = 0
    for k, (rr, cnt) in enumerate(INCIDENT_RINGS):
        a0 = rng.uniform(0, 2 * math.pi)
        for i in range(cnt):
            a = a0 + 2 * math.pi * i / cnt + rng.uniform(-0.08, 0.08)
            r = rr + rng.uniform(-0.12, 0.12)
            px, py = INCIDENT[0] + r * math.cos(a), INCIDENT[1] + r * math.sin(a)
            if any(math.hypot(px - qx, py - qy) < 0.7 for qx, qy in placed):
                continue
            placed.append((px, py))
            meta.append({"name": f"person_incident_{n:02d}", "route": "incident", "s": None,
                         "x": round(px, 3), "y": round(py, 3),
                         "yaw": round(math.atan2(INCIDENT[1] - py, INCIDENT[0] - px) + rng.uniform(-0.3, 0.3), 4),
                         "clear_x": None, "clear_y": None, "fuel": False})
            n += 1
    counts["incident"] = n
    return counts, meta


def group_people(meta, rng):
    """Merge the one-mesh people beyond the scan zone into small groups (same avenue,
    same side, within GROUP_LEN along it). Each group is ONE model that steps aside as
    a whole, which cuts the number of models Gazebo draws and moves by ~5x."""
    def single(p):     # scan zone + incident circle stay individual people
        return p["fuel"] or p["route"] == "incident" or p["s"] <= CROWD_RANGE[1]
    singles = [p for p in meta if single(p)]
    rest = [p for p in meta if not single(p)]
    groups = []
    for rname, r in ROUTES.items():
        line = _dedup(list(r["wp"]) + [INCIDENT])
        for side in (1, -1):
            ps = []
            for p in rest:
                if p["route"] != rname:
                    continue
                x, y, h = along(line, p["s"])
                off = -(p["x"] - x) * math.sin(h) + (p["y"] - y) * math.cos(h)
                if (off >= 0) == (side > 0):
                    ps.append((p, off * side, h))
            ps.sort(key=lambda t: t[0]["s"])
            cur = []
            for item in ps:
                if cur and (item[0]["s"] - cur[0][0]["s"] > GROUP_LEN or len(cur) >= GROUP_MAX):
                    groups.append((rname, side, cur))
                    cur = []
                cur.append(item)
            if cur:
                groups.append((rname, side, cur))
    out, k = list(singles), {}
    for rname, side, members in groups:
        n = k.get(rname, 0)
        k[rname] = n + 1
        gx = sum(m[0]["x"] for m in members) / len(members)
        gy = sum(m[0]["y"] for m in members) / len(members)
        s_mid = sum(m[0]["s"] for m in members) / len(members)
        h = members[0][2]
        nx, ny = -math.sin(h) * side, math.cos(h) * side       # towards the chosen roadside
        shift = (ROAD_W / 2 + 0.9) - min(m[1] for m in members)
        cxg, cyg = gx + nx * shift, gy + ny * shift
        if in_plaza((cxg, cyg), 0.3):
            continue
        out.append({"name": f"person_{rname}_g{n:02d}", "route": rname, "s": round(min(m[0]["s"] for m in members), 3),
                    "x": round(gx, 3), "y": round(gy, 3), "yaw": 0.0,
                    "clear_x": round(cxg, 3), "clear_y": round(cyg, 3), "fuel": False, "group": True,
                    "members": [(m[0]["x"] - gx, m[0]["y"] - gy, m[0]["yaw"]) for m in members],
                    "size": len(members), "s_mid": s_mid})
    return out


def _group_mesh(members, rng):
    m = Mesh()
    for dx, dy, yaw in members:
        src = VARIANT_MESHES[rng.randrange(len(VARIANT_MESHES))]
        c, s_ = math.cos(yaw), math.sin(yaw)
        vb, nb = len(m.v), len(m.n)
        m.v.extend((dx + c * x - s_ * y, dy + s_ * x + c * y, z) for x, y, z in src.v)
        m.t.extend(src.t)
        m.n.extend((c * x - s_ * y, s_ * x + c * y, z) for x, y, z in src.n)
        m.f.extend((i0 + vb, k, ni + nb) for i0, k, ni in src.f)
    return m


def mesh_people_sdf(people, files, rng):
    mat = (f"<material><ambient>1 1 1 1</ambient><diffuse>1 1 1 1</diffuse><specular>0.05 0.05 0.05 1</specular>"
           f"<pbr><metal><albedo_map>{ASSETS / 'walkers/atlas.png'}</albedo_map><roughness>0.85</roughness>"
           f"<metalness>0.0</metalness></metal></pbr></material>")
    out = ""
    gdir = ASSETS / "groups"
    gdir.mkdir(parents=True, exist_ok=True)
    for old in gdir.glob("*.obj"):
        old.unlink()
    for p in people:
        if p.get("group"):
            f = gdir / f"{p['name']}.obj"
            _group_mesh(p["members"], rng).write(f)
        else:
            f = files[rng.randrange(len(files))]
        out += (f"<model name='{p['name']}'><static>true</static>{pose(p['x'], p['y'], 0, yw=p['yaw'])}"
                f"<link name='link'><visual name='v'><geometry><mesh><uri>{f}</uri></mesh></geometry>"
                f"{mat}</visual></link></model>\n")
    return out


# =============================================================================
# SDF assembly
# =============================================================================
def mat_xml(name, tdir):
    m = MATS[name]
    tex = m.get("tex")
    c = (1.0, 1.0, 1.0) if tex else m["color"]
    cs = f"{c[0]:.3f} {c[1]:.3f} {c[2]:.3f} 1"
    albedo = f"<albedo_map>{tdir / (tex + '.png')}</albedo_map>" if tex else ""
    return (f"<material><ambient>{cs}</ambient><diffuse>{cs}</diffuse><specular>0.08 0.08 0.08 1</specular>"
            f"<pbr><metal>{albedo}<roughness>{m.get('rough', 0.85)}</roughness>"
            f"<metalness>0.0</metalness></metal></pbr></material>")


def write_meshes(tdir):
    mdir = ASSETS / "meshes"
    mdir.mkdir(parents=True, exist_ok=True)
    for old in mdir.glob("*.obj"):
        old.unlink()
    vis, manifest, tris = [], {}, 0
    for name, mesh in sorted(S.meshes.items()):
        if not mesh.f:
            continue
        path = mdir / f"{name}.obj"
        mesh.write(path)
        tris += mesh.tris()
        sh = "true" if MATS[name].get("shadow", True) else "false"
        vis.append(f"<visual name='{name}'><cast_shadows>{sh}</cast_shadows>"
                   f"<geometry><mesh><uri>{path}</uri></mesh></geometry>{mat_xml(name, tdir)}</visual>")
        manifest[name] = dict(MATS[name], obj=str(path),
                              tex_path=str(tdir / (MATS[name]["tex"] + ".png")) if "tex" in MATS[name] else None)
    (ASSETS / "manifest.json").write_text(json.dumps(manifest, indent=1))
    sdf = ("<model name='expo_scenery'><static>true</static><link name='link'>"
           + "".join(vis) + "</link></model>\n")
    coll = "".join(
        f"<collision name='c{i}'>{pose(cx, cy, z0 + sz / 2, yw=yaw)}"
        f"<geometry><box><size>{sx:.2f} {sy:.2f} {sz:.2f}</size></box></geometry></collision>"
        for i, (cx, cy, z0, sx, sy, sz, yaw) in enumerate(S.coll))
    sdf += f"<model name='expo_buildings'><static>true</static><link name='link'>{coll}</link></model>\n"
    return sdf, len(vis), tris


def patch_base(src, shadows=True):
    src, n = re.subn(r'<world name="[^"]*">', f'<world name="{WORLD_NAME}">', src, count=1)
    if n != 1 or "</world>" not in src:
        raise SystemExit(f"Could not parse {SRC}")
    gp = re.search(r"<model name=[\"']ground_plane[\"']>.*?</model>", src, flags=re.S)
    if not gp:
        raise SystemExit("ground_plane model not found in default.sdf - cannot hide its grey visual")
    block = re.sub(r"<visual\b.*?</visual>", "", gp.group(0), flags=re.S)
    src = src[:gp.start()] + block + src[gp.end():]
    if re.search(r"<scene>.*?</scene>", src, flags=re.S):
        src = re.sub(r"<scene>.*?</scene>", scene_xml(shadows), src, count=1, flags=re.S)
    else:
        src = src.replace(f'<world name="{WORLD_NAME}">', f'<world name="{WORLD_NAME}">' + scene_xml(shadows), 1)
    return src


# =============================================================================
# Contract checks
# =============================================================================
def in_coll_one(p, b, pad):
    cx, cy, sx, sy, yaw = b
    c, s = math.cos(-yaw), math.sin(-yaw)
    dx, dy = p[0] - cx, p[1] - cy
    return abs(dx * c - dy * s) <= sx / 2 + pad and abs(dx * s + dy * c) <= sy / 2 + pad


def check(meta):
    ok = True
    yaws = {k: v["scan_yaw_enu_deg"] for k, v in meta["routes"].items()}
    names = list(yaws)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            d = abs((yaws[names[i]] - yaws[names[j]] + 180) % 360 - 180)
            if d < 30:
                print(f"CHECK FAIL: {names[i]} / {names[j]} only {d:.1f} deg apart")
                ok = False
    hx, hy = HOVER_POINT
    for p in meta["people"]:
        if p["route"] == "incident":
            continue
        d = math.hypot(p["x"] - hx, p["y"] - hy)
        if p["s"] <= CROWD_RANGE[1] and not 10 <= d <= 40:
            print(f"CHECK FAIL: {p['name']} is {d:.1f} m from the hover point")
            ok = False
        for q in ((p["x"], p["y"]), (p["clear_x"], p["clear_y"])):
            if in_coll(q, 0.3):
                print(f"CHECK FAIL: {p['name']} at/clears into a structure {q}")
                ok = False
    for r in ROUTES:
        path = cart_path(r)
        L = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in path_segs(path))
        s = 0.0
        while s <= L + LEAD:
            x, y, _ = along(path, min(s, L))
            for cx, cy, z0, sx, sy, sz, yaw in S.coll:
                if z0 + sz > 7.0 and in_coll_one((x, y), (cx, cy, sx, sy, yaw), 6.0):
                    print(f"CHECK FAIL: route {r} passes within 6 m of a {z0 + sz:.1f} m structure at ({cx},{cy})")
                    ok = False
                    break
            s += 1.0
    for cx, cy, z0, sx, sy, sz, yaw in S.coll:
        if sz < 2.0:
            continue  # perimeter wall
        c, s_ = math.cos(yaw), math.sin(yaw)
        for kx in (-0.5, 0.5):
            for ky in (-0.5, 0.5):
                q = (cx + c * kx * sx - s_ * ky * sy, cy + s_ * kx * sx + c * ky * sy)
                if street_dist(q) < ROAD_W / 2 + 0.5:
                    print(f"CHECK FAIL: structure at ({cx},{cy}) reaches onto an avenue")
                    ok = False
    for v in meta.get("ambient", []):
        if street_dist(v) < ROAD_W / 2 + 0.5 and not in_plaza(v):
            print(f"CHECK FAIL: ambient visitor at {v} stands on an avenue")
            ok = False
            break
    phys = {3.6: 3.5, 2.2: 0.3, 2.0: 1.9, 1.8: 0.3, 1.6: 0.5, 1.5: 0.5, 1.4: 0.3, 1.0: 0.95,
            0.8: 0.7, 0.5: 0.2, 0.35: 0.15}
    for x, y, r in S.props:
        need = phys.get(r, 0.0) + 0.3
        if r < 5 and near_walk((x, y), need):
            print(f"CHECK FAIL: walkers' lane passes through a prop at ({x:.1f},{y:.1f}) r={r}")
            ok = False
    print("Layout checks:", "OK" if ok else "FAILED (see above)")
    return ok


# =============================================================================
# Main
# =============================================================================
def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--test", action="store_true", help="4 test people only")
    g.add_argument("--no-people", action="store_true", help="no people at all")
    ap.add_argument("--lite", action="store_true", help="fewer trees, palms and lamps (slow laptops)")
    ap.add_argument("--shadows", action="store_true", help="turn shadows on (slower)")
    ap.add_argument("--fuel", action="store_true",
                    help="use the detailed Fuel person models in the scan zone (slower)")
    ap.add_argument("--rebuild-textures", action="store_true")
    args = ap.parse_args()
    USE_FUEL[0] = args.fuel

    src = patch_base(SRC.read_text(), shadows=args.shadows)
    tdir = build_textures(args.rebuild_textures)

    counts = {k: 0 for k in ROUTES}
    counts["incident"] = 0
    people_meta, people_sdf, paths, ambient = [], "", [], []
    cx, cy, cyaw = CART_POSE
    hx, hy = HOVER_POINT
    if not args.no_people:
        models = resolve_people()
        if not models:
            raise SystemExit("No people models found locally. Run the gz fuel download "
                             "commands first, or use --no-people.")
        paths = list(models.values())
        if args.test:
            fx, fy = math.cos(cyaw), math.sin(cyaw)
            for i, off in enumerate([-3, -1, 1, 3]):
                px, py = hx + fx * 8.0 - fy * off, hy + fy * 8.0 + fx * off
                people_sdf += person(paths[i % len(paths)], f"test_person_{i}", px, py, cyaw + math.pi)
                S.reserved.append((px, py))
        else:
            rng = random.Random(SEED)
            counts, people_meta = plan_crowds(rng)
            for p in people_meta:
                if p["fuel"]:
                    people_sdf += person(rng.choice(paths), p["name"], p["x"], p["y"], p["yaw"])
                S.reserved.append((p["x"], p["y"]))
                if p["clear_x"] is not None:
                    S.reserved.append((p["clear_x"], p["clear_y"]))
    S.reserved.append(tuple(CART_POSE[:2]))
    S.props.append((INCIDENT[0], INCIDENT[1], 6.0))  # patient, medics, stretcher area

    rng = random.Random(SEED + 1)
    # The routes and everything between them (gardens, shade canopies, fountains,
    # kiosks, palms and lamps along the avenues) + the First Aid Center (medics' base).
    # Nothing outside the routes: no side pavilions, dome, wall, parks or bystanders.
    ground(args.lite)
    first_aid_center()
    inner_zones(rng, args.lite)
    streetscape(rng, args.lite, palms=True)
    scenery, nvis, tris = write_meshes(tdir)

    walkers = []
    if paths and not args.test:
        atlas, files = build_walker_assets()
        people_meta = group_people(people_meta, random.Random(SEED + 5))
        people_sdf += mesh_people_sdf([p for p in people_meta if not p["fuel"]], files, random.Random(SEED + 4))
    if paths:
        people_sdf += rescue_sdf(paths)

    content = "\n<!-- ===== expo_demo content (generated by gen_world.py) ===== -->\n"
    content += scenery + golf_cart() + people_sdf
    hx0, hy0 = cx + DRONE_SPAWN_X_OFF * math.cos(cyaw), cy + DRONE_SPAWN_X_OFF * math.sin(cyaw)
    content += marker_sdf(hx0, hy0, DRONE_SPAWN_Z + MARKER_DZ)
    OUT.write_text(src.replace("</world>", content + "</world>", 1))

    home = (cx + DRONE_SPAWN_X_OFF * math.cos(cyaw), cy + DRONE_SPAWN_X_OFF * math.sin(cyaw))
    home = (round(home[0], 3) + 0.0, round(home[1], 3) + 0.0)
    meta = {"world": WORLD_NAME, "road_w": ROAD_W,
            "home_enu": home, "hover_point_enu": HOVER_POINT,
            "incident_enu": INCIDENT, "cart_start_enu": [cx, cy, round(cyaw, 4)],
            "routes": {}, "people": [{k: v for k, v in p.items() if k not in ("fuel", "members", "s_mid")}
                                     for p in people_meta],
            "first_aid": {"door_enu": FA_DOOR, "door_heading": round(math.pi, 4),
                          "inside_enu": FA_INSIDE, "medic_run_enu": MEDIC_RUN,
                          "cart_route_enu": CART_FA_ROUTE},
            "patient_enu": PATIENT,
            "rescue_models": ({"medics": ["medic_0", "medic_1"], "stretcher": "stretcher",
                               "patient": "patient"} if paths else {}),
            "walkers": [{k: w[k] for k in ("name", "loop", "speed", "phase")} for w in walkers],
            "marker": {"name": "drone_marker", "dz": MARKER_DZ},
            "ambient": ambient}
    for rname, r in ROUTES.items():
        mx, my, _ = along(r["wp"], sum(CROWD_RANGE) / 2)
        enu = math.degrees(math.atan2(my - hy, mx - hx))
        meta["routes"][rname] = {
            "waypoints_enu": r["wp"],
            "people": counts.get(rname, 0),
            "scan_yaw_enu_deg": round(enu, 1),
            "scan_yaw_ned_deg": round((90 - enu) % 360, 1),  # PX4 yaw
        }
    META.parent.mkdir(parents=True, exist_ok=True)
    META.write_text(json.dumps(meta, indent=1))

    mode = "NO-PEOPLE" if args.no_people else ("TEST" if args.test else "FULL")
    print(f"Wrote {OUT}  ({mode}{', LITE' if args.lite else ''} mode)")
    print(f"      scenery: {nvis} meshes, {tris / 1000:.0f}k triangles, {S.palms} palms, "
          f"{len(S.coll)} collision boxes")
    n_fuel = sum(1 for p in people_meta if p["fuel"])
    n_grp = sum(1 for p in people_meta if p.get("group"))
    n_ppl = sum(p.get("size", 1) for p in people_meta)
    print(f"      people: {n_ppl} on the avenues + round the injured in {len(people_meta)} models "
          f"({n_fuel} Fuel, {n_grp} groups, {len(people_meta) - n_fuel - n_grp} singles), "
          f"{4 if paths else 0} rescue models")
    print(f"Wrote {META}")
    for k, v in meta["routes"].items():
        print(f"  {k:9s} people={v['people']:3d}  scan yaw NED={v['scan_yaw_ned_deg']}°")
    print(f"  incident  people={counts['incident']:3d}  (packed round the injured visitor)")
    if not args.test:
        check(meta)
    # spawn yaw stays 0 (east): a rotated spawn makes PX4 SITL fail with "Yaw estimate error"
    spawn = f"{home[0]:g},{home[1]:g},{DRONE_SPAWN_Z:.2f},0,0,0"
    print(f"\nDrone spawn pose (used by launch_sim.sh): {spawn}")


if __name__ == "__main__":
    main()
