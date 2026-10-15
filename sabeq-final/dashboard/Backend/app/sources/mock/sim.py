"""Shared mock-simulation helpers: the tick loop and the world's physical constants.

Every mock runs one asyncio task on the event loop. Each tick happens every
MOCK_TICK_S real seconds and advances simulated time by MOCK_TICK_S * MOCK_SPEED.

The mocks simulate in world ENU metres (app/world.py) and project to Position only
when they report. Timings mirror docs/team/mission.py (drone) and sim_node.py (world);
the source constant is named next to each value.
"""
import asyncio
import logging
import math
from typing import Callable

from app.config import Settings
from app.state.machine import InvalidTransition
from app.world import project

log = logging.getLogger(__name__)

# --- drone (mission.py) ---------------------------------------------------------
TAKEOFF_MPS = 3.5  # MPC_TKO_SPEED
AIRBORNE_M = 2.0  # the world resets once the drone is this high (mission.py resets after take-off)
DRONE_MAX_MPS = 14.0  # MPC_XY_VEL_MAX
YAW_DPS = 180.0  # MPC_YAWRAUTO_MAX
SCAN_YAW_TOL_DEG = 8.0  # SCAN_YAW_TOL
SCAN_SETTLE_S = 0.15  # SCAN_SETTLE
SCAN_HOLD_S = 1.5  # per heading; no YOLO latency in the mock (ROS port: 1.0-10.0 s, ROS_TOPICS §4.4)
SCAN_POINT_TOL_M = 1.5  # "at the scan point"
LEAD_M = 15.0  # LEAD: the drone flies this far ahead of the cart
OBSERVE_BACK_M = 8.0  # OBSERVE_BACK: watch point behind the stopped cart
ROOF_HOVER_M = 1.8  # ROOF_HOVER: above the cart roof before the final descent
DESCENT_MPS = 2.5  # MPC_Z_VEL_MAX_DN
LAND_MPS = 1.5  # MPC_LAND_SPEED
OVER_CART_M = 4.0  # land_on_cart: come over the cart at escort height first when farther than this

# --- cart and medics (sim_node.py) ------------------------------------------------
CART_SPEED_MPS = 10.0  # mission.py CART_SPEED (cart_start speed)
RETURN_SPEED_MPS = 10.0  # RETURN_SPEED
SLOW_ZONE_M = 12.0  # SLOW_ZONE: ease in before the stop
SLOW_MIN_MPS = 2.5
GAP_STOP_M = 9.0  # GAP_STOP: the cart waits if the drone is less than this far ahead
GAP_FULL_M = 12.0  # GAP_FULL: full speed once the drone is this far ahead
DRONE_NEAR_PATH_M = 12.0  # World.drone_s: farther from the path = unknown, don't wait
TURN_TIME_S = 1.2  # TURN_TIME
WALK_MPS = 3.0  # WALK_SPEED (medics with the stretcher)
TREAT_S = 1.5  # TREAT_TIME
LOAD_S = 0.8  # LOAD_TIME
BED_X_M = -2.3  # demo_common.BED_X: stretcher on the rear bed (cart frame)
SIDE_STEP_M = 2.2  # Rescue.dismount: medics hop off the bed sideways
PATIENT_SIDE_M = 1.0  # Rescue.dismount: they work 1 m beside the patient

# --- battery (unchanged demo model) -------------------------------------------------
DRAIN_PCT_PER_S = 0.08
CHARGE_PCT_PER_S = 0.5
FULL_FLIGHT_S = 1560.0  # 26 min on a full battery


def lead_factor(path, length_m: float, cart_s: float, drone_local) -> float:
    """sim_node.World.lead_factor: 0..1, slows / stops the cart until the drone is 9-12 m
    ahead of it. drone_local None (no drone flying) means don't wait."""
    if drone_local is None:
        return 1.0
    drone_s, off = project(path, drone_local)
    if off >= DRONE_NEAR_PATH_M:
        return 1.0
    stop = min(GAP_STOP_M, length_m - cart_s - 1.0)
    full = min(GAP_FULL_M, length_m - cart_s)
    if drone_s >= length_m - 1.0 or full <= stop:
        return 1.0
    return max(0.0, min(1.0, (drone_s - cart_s - stop) / (full - stop)))


def rescue_duration_s(cart_xy, cart_heading_rad: float, patient) -> float:
    """sim_node.Rescue at the incident: hop off the bed sideways, walk to the patient,
    treat, load, walk back to the bed side. Returns the seconds until READY."""
    c, s = math.cos(cart_heading_rad), math.sin(cart_heading_rad)
    bed = (cart_xy[0] + c * BED_X_M, cart_xy[1] + s * BED_X_M)
    left = (-s, c)
    px, py, pyaw = patient
    normal = (-math.sin(pyaw), math.cos(pyaw))
    spots = [(px + normal[0] * k, py + normal[1] * k) for k in (PATIENT_SIDE_M, -PATIENT_SIDE_M)]
    spot = min(spots, key=lambda q: math.dist(q, bed))
    side = 1.0 if (spot[0] - bed[0]) * left[0] + (spot[1] - bed[1]) * left[1] >= 0 else -1.0
    side_wp = (bed[0] + left[0] * side * SIDE_STEP_M, bed[1] + left[1] * side * SIDE_STEP_M)
    walk = math.dist(side_wp, spot) / WALK_MPS
    return walk + TREAT_S + LOAD_S + walk


def move_towards(pos, target, max_step: float) -> tuple:
    """(new position, metres moved) after moving at most max_step towards target."""
    d = math.dist(pos, target)
    if d <= max_step or d == 0:
        return tuple(target), d
    k = max_step / d
    return (pos[0] + (target[0] - pos[0]) * k, pos[1] + (target[1] - pos[1]) * k), max_step


class Ticker:
    """Runs step(sim_dt) every tick on the event loop until stopped."""

    def __init__(self, settings: Settings, name: str):
        self.settings = settings
        self.name = name
        self._task: asyncio.Task | None = None

    def start(self, step: Callable[[float], None]) -> None:
        self._task = asyncio.create_task(self._run(step), name=f"mock-{self.name}")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self, step: Callable[[float], None]) -> None:
        tick = self.settings.mock_tick_s
        while True:
            await asyncio.sleep(tick)
            try:
                step(tick * self.settings.mock_speed)
            except InvalidTransition as exc:
                # The operator (or another source) changed state first; not a bug.
                log.debug("mock %s: %s", self.name, exc)
            except Exception:
                log.exception("mock %s step failed", self.name)
