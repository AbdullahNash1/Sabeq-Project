#!/usr/bin/env python3
"""
mission.py - the drone side of the demo. Runs WITHOUT the venv (uses MAVSDK).

  python3 ~/emergency_drone/mission.py

Needs: sim running (launch_sim.sh) and sim_node.py running (in the venv).

Re-run it as many times as you like: each run resets the cart and people
(via sim_node.py) and flies the drone back to the scan point from wherever
it landed. Terminals 1 and 2 stay running.

Sequence:
  0. Set SITL-friendly PX4 parameters (no RC in the sim -> no RC failsafes,
     brief link stalls -> hover instead of land).
  1. Take off from the golf cart roof to ALT metres.
  2. Scan (~6 s): yaw to each of the 3 routes, measure crowd density with YOLO
     (via sim_node). Meanwhile the medics run from the First Aid Center to the cart.
  3. Pick the route with the lowest density (YOLO is switched off after this).
  4. Siren ON, start the golf cart (it waits until the drone is 9+ m ahead).
  5. Escort: stay LEAD metres ahead of the cart along the route; people on the
     route step aside as the drone reaches them.
  6. Cart arrives at the incident: siren OFF, hover behind the cart and watch the
     medics load the patient.
  7. Escort back: siren ON, stay LEAD metres ahead of the cart on the way back to
     the First Aid Center.
  8. Land on the golf cart roof at the First Aid Center.

Re-runs are fast: the drone takes off from wherever it is (normally the cart roof)
BEFORE the world is reset, so the cart is never teleported under a landed drone.
PX4 stays in LAND mode after a landing and refuses to arm in it, so the mission
switches PX4 to HOLD after landing and again before arming.
Ctrl+C stops the mission with the drone hovering in place (HOLD); just run the
script again to start a new mission from the air.

Every PX4 warning and flight-mode change is printed as [PX4] / [MODE] lines.
"""
import asyncio
import json
import signal
import subprocess
import math
import socket
import statistics
import time

from mavsdk import System
from mavsdk.offboard import OffboardError, PositionNedYaw, VelocityNedYaw

import demo_common as dc

CONNECTION = "udpin://0.0.0.0:14540"   # older MAVSDK versions: "udp://:14540"
ALT = 8.0            # m above the cart roof (scan + escort height)
SCAN_YAW_TOL = 8.0   # deg: start counting once the drone faces the route this closely
SCAN_SETTLE = 0.15   # s after reaching the heading
SCAN_SAMPLE = 0.8    # s of counting per route  (3 routes x ~1.3 s ~ 4 s)
SCAN_MIN_FRAMES = 2  # fresh YOLO results needed per route (waits longer if YOLO is slow)
SCAN_MAX_WAIT = 2.5  # s max per route
LEAD = 15.0          # m the drone flies ahead of the cart
CART_SPEED = 10.0    # m/s (36 km/h, routes are 110-160 m)
OBSERVE_BACK = 8.0   # m behind the stopped cart where the drone watches the rescue
RESCUE_TIMEOUT = 120.0
ROOF_HOVER = 1.8     # m above the cart roof before the final landing
ROOF_X = -0.1        # landing spot on the roof (cart frame, m)

# PX4 parameters for the simulation demo (saved by SITL, so they persist between runs)
SIM_PARAMS_INT = {
    "COM_RC_IN_MODE": 4,   # no RC stick input in the sim -> no "RC lost" failsafe
    "COM_RCL_EXCEPT": 4,   # ignore RC loss while in offboard
    "COM_OBL_RC_ACT": 5,   # if offboard commands stop: HOLD (hover), not position mode
    "NAV_DLL_ACT": 0,      # no action on ground-station link loss
}
SIM_PARAMS_FLOAT = {        # limits first: PX4 clamps the dependent ones to them
    "COM_OF_LOSS_T": 3.0,       # s without offboard commands before failsafe (default 1 s)
    "MPC_Z_VEL_MAX_UP": 4.0,    # m/s climb (default 3)
    "MPC_Z_VEL_MAX_DN": 2.5,    # m/s descent (default 1.5)
    "MPC_XY_VEL_MAX": 14.0,     # m/s: room to lead the 10 m/s cart (default 12)
    "MPC_XY_CRUISE": 13.0,      # m/s (default 5)
    "MPC_YAWRAUTO_MAX": 180.0,  # deg/s yaw rate: faster scan (default 45)
    "MPC_YAWRAUTO_ACC": 360.0,  # deg/s^2 (parameter only exists on newer PX4)
    "MPC_TKO_SPEED": 3.5,       # m/s takeoff climb (default 1.5)
    "MPC_LAND_SPEED": 1.5,      # m/s final descent (default 0.7)
    "COM_DISARM_LAND": 0.5,     # s on the roof before disarming (default 2)
}


class Link:
    """UDP link to sim_node.py."""

    def __init__(self):
        self.rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.rx.bind(("127.0.0.1", dc.STATUS_PORT))
        self.rx.setblocking(False)
        self.tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.status = {}
        self.last = 0.0

    def poll(self):
        while True:
            try:
                data, _ = self.rx.recvfrom(65535)
            except BlockingIOError:
                return self.status
            self.status = json.loads(data)
            self.last = time.time()

    def alive(self):
        self.poll()
        return time.time() - self.last < 1.0

    def send(self, **cmd):
        self.tx.sendto(json.dumps(cmd).encode(), ("127.0.0.1", dc.CMD_PORT))


class Vehicle:
    """One subscription per telemetry stream for the whole mission.

    Opening and closing the same MAVSDK stream over and over (or subscribing to
    one stream twice) can crash mavsdk_server ("Connection reset by peer"), so
    every value the mission needs is cached here and read from the cache.
    """

    def __init__(self, drone):
        self.drone = drone
        self.armed = None
        self.in_air = None
        self.mode = None
        self.rel_alt = None
        self.pv = None          # PositionVelocityNed
        self.yaw = None         # deg
        self.lost = None        # error message if the link to mavsdk_server died

    def start(self):
        streams = ((self._status_text, "status_text"), (self._mode, "flight_mode"),
                   (self._armed, "armed"), (self._in_air, "in_air"), (self._pos, "position"),
                   (self._pv, "position_velocity_ned"), (self._att, "attitude_euler"))
        for fn, name in streams:
            asyncio.ensure_future(self._guard(fn, name))

    async def _guard(self, fn, name):
        try:
            await fn()
        except asyncio.CancelledError:
            pass
        except Exception as e:  # noqa: BLE001
            if self.lost is None:
                self.lost = f"{name} stream: {type(e).__name__}"

    async def _status_text(self):
        async for st in self.drone.telemetry.status_text():
            print(f"[PX4] {st.type.name}: {st.text}")

    async def _mode(self):
        async for m in self.drone.telemetry.flight_mode():
            if m != self.mode:
                print(f"[MODE] {m.name}")
                self.mode = m

    async def _armed(self):
        async for a in self.drone.telemetry.armed():
            self.armed = a

    async def _in_air(self):
        async for a in self.drone.telemetry.in_air():
            self.in_air = a

    async def _pos(self):
        async for p in self.drone.telemetry.position():
            self.rel_alt = p.relative_altitude_m

    async def _pv(self):
        async for pv in self.drone.telemetry.position_velocity_ned():
            self.pv = pv

    async def _att(self):
        async for a in self.drone.telemetry.attitude_euler():
            self.yaw = a.yaw_deg

    async def ready(self, timeout=15.0):
        t0 = time.time()
        while None in (self.armed, self.in_air, self.rel_alt, self.pv, self.yaw, self.mode):
            if self.lost:
                raise SystemExit(f"Lost connection to mavsdk_server ({self.lost}). Re-run mission.py.")
            if time.time() - t0 > timeout:
                raise SystemExit("No telemetry from PX4 - is Terminal 1 (pxh>) running?")
            await asyncio.sleep(0.05)

    def in_offboard(self):
        return self.mode is not None and self.mode.name == "OFFBOARD"


async def first(gen):
    async for item in gen:
        return item


async def goto(drone, n, e, yaw, down=None):
    await drone.offboard.set_position_ned(PositionNedYaw(n, e, -ALT if down is None else down, yaw))


async def lead_cart(drone, path, L, s_cart, v_cart, home):
    """Fly LEAD m ahead of the cart. The cart's speed is sent as a velocity feed-forward:
    with a position target alone PX4 trails it by ~1 m per m/s, the drone ends up too
    close to the cart, and sim_node holds the cart back (that made the escort crawl)."""
    s = min(s_cart + LEAD, L)
    x, y, h = dc.along(path, s)
    n, e = dc.enu_to_ned(x, y, home)
    yaw = dc.heading_to_yaw_ned_deg(h)
    v = v_cart if s < L - 0.5 else 0.0
    try:
        await drone.offboard.set_position_velocity_ned(
            PositionNedYaw(n, e, -ALT, yaw), VelocityNedYaw(v * math.sin(h), v * math.cos(h), 0.0, yaw))
    except AttributeError:      # very old MAVSDK
        await goto(drone, n, e, yaw)


async def _ensure_param(get, set_, name, val, same):
    try:
        cur = await asyncio.wait_for(get(name), 2.0)
        if same(cur, val):
            return False
        await asyncio.wait_for(set_(name, val), 2.0)
        return True
    except Exception as e:  # noqa: BLE001  (unknown on this PX4 version, or timeout)
        print(f"  skipped {name}: {type(e).__name__}")
        return False


async def set_sim_params(drone):
    """Only writes parameters that differ (PX4 SITL keeps them between runs)."""
    p = drone.param
    changed = 0
    for name, val in SIM_PARAMS_INT.items():
        changed += await _ensure_param(p.get_param_int, p.set_param_int, name, val, lambda a, b: a == b)
    for name, val in SIM_PARAMS_FLOAT.items():
        changed += await _ensure_param(p.get_param_float, p.set_param_float, name, val,
                                       lambda a, b: abs(a - b) < 1e-3)
    print(f"PX4 simulation parameters ok ({changed} changed)")


async def land_on_cart(drone, veh, link, home):
    """Hover above the golf cart roof, then land on it."""
    st = link.poll()
    cart = st.get("cart")
    if not cart:
        print("No cart position from sim_node - landing in place.")
        await drone.action.land()
        return
    rx, ry = dc.cart_local(cart, ROOF_X, 0.0)
    n, e = dc.enu_to_ned(rx, ry, home)
    yaw = dc.heading_to_yaw_ned_deg(cart[2])
    print("Landing on the golf cart ...")
    p = veh.pv.position
    if math.hypot(p.north_m - n, p.east_m - e) > 4.0:   # far away: come over at escort height first
        await goto(drone, n, e, yaw)
        t0 = time.time()
        while time.time() - t0 < 12:
            check_flying(veh, "approach to the cart")
            p = veh.pv.position
            if math.hypot(p.north_m - n, p.east_m - e) < 4.0:
                break
            await asyncio.sleep(0.1)
    await goto(drone, n, e, yaw, down=-ROOF_HOVER)     # roof is at about the spawn height (down = 0)
    t0 = time.time()
    while time.time() - t0 < 12:
        check_flying(veh, "descent to the cart")
        p, v = veh.pv.position, veh.pv.velocity
        if (math.hypot(p.north_m - n, p.east_m - e) < 0.25 and abs(p.down_m + ROOF_HOVER) < 0.4
                and math.hypot(v.north_m_s, v.east_m_s) < 0.3):
            break
        await asyncio.sleep(0.1)
    await drone.action.land()
    t0 = time.time()
    while veh.armed and time.time() - t0 < 30:
        await asyncio.sleep(0.2)
    print("Landed on the golf cart." if not veh.armed else "Still armed after 30 s - check [PX4] messages.")
    await asyncio.sleep(1.0)
    if await ground_hold(drone, veh, " after landing"):     # so the next run can arm right away
        print("PX4 in HOLD - ready for the next run.")


async def ground_hold(drone, veh, why=""):
    """Put a landed drone in HOLD. PX4 stays in LAND (or RTL) mode after touching down
    and refuses to arm in those modes, which blocked every re-run."""
    if veh.mode is not None and veh.mode.name == "HOLD":
        return True
    try:
        await asyncio.wait_for(drone.action.hold(), 5.0)
    except Exception as e:  # noqa: BLE001
        print(f"  could not switch PX4 to HOLD{why}: {e}")
        return False
    t0 = time.time()
    while time.time() - t0 < 3.0:
        if veh.mode is not None and veh.mode.name == "HOLD":
            return True
        await asyncio.sleep(0.1)
    return False


async def arm_and_takeoff(drone, veh):
    """Arm (retrying through HOLD if PX4 says no) and take off to ALT."""
    await drone.action.set_takeoff_altitude(ALT)
    print("Arming and taking off ...")
    for attempt in range(4):
        try:
            if not veh.armed:
                await asyncio.wait_for(drone.action.arm(), 5.0)
            await asyncio.wait_for(drone.action.takeoff(), 5.0)
            break
        except Exception as e:  # noqa: BLE001
            print(f"  arm/takeoff refused ({e}) - switching to HOLD and retrying")
            await ground_hold(drone, veh)
            await asyncio.sleep(1.0)
    else:
        raise SystemExit("PX4 refused to arm 4 times - see the [PX4] messages above.")
    t0 = time.time()
    while time.time() - t0 < 25:
        if veh.rel_alt > ALT - 2.0:          # offboard climbs the last metres on the way
            break
        if not veh.armed and time.time() - t0 > 3:
            raise SystemExit("Drone disarmed during takeoff - see [PX4] messages above.")
        await asyncio.sleep(0.3)
    print(f"At {veh.rel_alt:.1f} m")


class Aborted(Exception):
    pass


def check_flying(veh, where):
    if veh.lost:
        raise Aborted(f"lost connection to mavsdk_server during {where} ({veh.lost})")
    if not veh.armed:
        raise Aborted(f"drone disarmed during {where}")
    if not veh.in_offboard():
        raise Aborted(f"drone left OFFBOARD mode during {where} (mode: {veh.mode.name})")


async def main():
    meta = dc.load_meta()
    home = meta["home_enu"]
    link = Link()

    print("Waiting for sim_node.py ...")
    for _ in range(50):
        if link.alive():
            break
        await asyncio.sleep(0.1)
    else:
        raise SystemExit("No data from sim_node.py. Start it first (in the venv).")

    print("sim_node.py OK. Connecting to PX4 ...")
    # a mavsdk_server left over from an interrupted run blocks the new one silently
    subprocess.run(["pkill", "-x", "mavsdk_server"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    await asyncio.sleep(0.5)
    drone = System()
    try:
        await asyncio.wait_for(drone.connect(system_address=CONNECTION), 15)

        async def _connected():
            async for st in drone.core.connection_state():
                if st.is_connected:
                    return
        await asyncio.wait_for(_connected(), 20)
    except asyncio.TimeoutError:
        raise SystemExit("PX4 did not answer in 20 s. Is Terminal 1 still showing pxh>? "
                         "If not, do the full restart.")
    print("PX4 connected.")
    veh = Vehicle(drone)
    veh.start()

    await set_sim_params(drone)

    print("Reading drone state ...")
    await veh.ready()
    print(f"Drone: {'flying' if veh.in_air else 'on the ground'}, {'armed' if veh.armed else 'disarmed'}, "
          f"{veh.rel_alt:.1f} m")
    if veh.in_air and veh.armed:
        print("Drone already flying - skipping takeoff.")
    else:
        if veh.mode.name != "HOLD":
            # left in LAND / RTL by the previous run: PX4 won't arm until the mode changes
            print(f"PX4 is in {veh.mode.name} mode from the last run - switching to HOLD ...")
            await ground_hold(drone, veh)
        print("Waiting until PX4 is ready to arm (estimator settling) ...")
        t0, last_hold, last_msg = time.time(), time.time(), 0
        async for hlt in drone.telemetry.health():
            if (hlt.is_global_position_ok and hlt.is_home_position_ok
                    and hlt.is_local_position_ok and (hlt.is_armable or veh.armed)):
                break
            if time.time() - t0 > 60:
                raise SystemExit("PX4 not ready after 60 s - check the [PX4] messages above.")
            if not hlt.is_armable and veh.mode.name != "HOLD" and time.time() - last_hold > 3:
                last_hold = time.time()
                await ground_hold(drone, veh)
            if int(time.time() - t0) // 10 > last_msg:
                last_msg = int(time.time() - t0) // 10
                print(f"  still waiting: global={hlt.is_global_position_ok} home={hlt.is_home_position_ok} "
                      f"local={hlt.is_local_position_ok} armable={hlt.is_armable} mode={veh.mode.name}")

        # ---- 1. Take off ---------------------------------------------------
        await arm_and_takeoff(drone, veh)

    # Reset the world (cart, people, medics back to the start) only now that the drone
    # is in the air: the cart is never teleported from under (or onto) a landed drone.
    link.send(cmd="reset")
    await asyncio.sleep(0.3)

    # Switch to offboard and fly to the scan point above the plaza
    # (on a re-run the drone starts from wherever it landed last time)
    await goto(drone, 0.0, 0.0, veh.yaw)
    try:
        await drone.offboard.start()
    except OffboardError as e:
        print(f"Offboard failed: {e._result.result}. Landing.")
        await drone.action.land()
        return
    print("Going to the scan point ...")
    t0 = time.time()
    while time.time() - t0 < 60:
        p = veh.pv.position
        if math.hypot(p.north_m, p.east_m) < 1.5 and abs(p.down_m + ALT) < 1.0:
            break
        if not veh.armed:
            raise SystemExit("Drone disarmed on the way to the scan point - see [PX4] messages.")
        await asyncio.sleep(0.3)
    await asyncio.sleep(0.5)

    finished = False
    try:
        # ---- 2. Scan ------------------------------------------------------
        print("Scanning routes ...")
        scan_t0 = time.time()
        counts, density = {}, {}
        for rname, r in meta["routes"].items():
            link.send(cmd="scanning", route=rname)
            target = r["scan_yaw_ned_deg"]
            await goto(drone, 0.0, 0.0, target)
            t_yaw = time.time()
            while time.time() - t_yaw < 3.0:
                if abs((veh.yaw - target + 180) % 360 - 180) < SCAN_YAW_TOL:
                    break
                await asyncio.sleep(0.05)
            await asyncio.sleep(SCAN_SETTLE)
            check_flying(veh, "scan")
            ready = time.time()   # only use camera frames taken after this moment
            c_samples, d_samples, seen = [], [], set()
            while True:
                st = link.poll()
                det_t = st.get("det_t", 0.0)
                if det_t >= ready and det_t not in seen:   # a new, fresh detection
                    seen.add(det_t)
                    c_samples.append(st.get("count_center", 0))
                    d_samples.append(st.get("coverage", 0.0))
                waited = time.time() - ready
                if (waited >= SCAN_SAMPLE and len(seen) >= SCAN_MIN_FRAMES) or waited >= SCAN_MAX_WAIT:
                    break
                await asyncio.sleep(0.05)
            link.send(cmd="snapshot", route=rname)
            counts[rname] = max(c_samples) if c_samples else 0      # misses are the main error
            density[rname] = statistics.median(d_samples) if d_samples else 0.0
            print(f"  {rname:9s} -> {counts[rname]:2d} people, density {density[rname]:5.1f}%  "
                  f"({len(seen)} frames)")
            if not c_samples:
                print("    WARNING: no fresh detections - YOLO too slow or sim_node stuck")

        # ---- 3. Choose (lowest density; people count breaks ties) -----------
        print(f"Scan took {time.time() - scan_t0:.1f} s")
        # Rank each route by crowd density (share of the band covered by people) AND by
        # people counted; the lowest combined rank wins (density breaks ties). Using both
        # makes the choice robust when YOLO merges or misses people in a dense crowd.
        names = list(density)
        r_d = {k: sorted(names, key=lambda n: density[n]).index(k) for k in names}
        r_c = {k: sorted(names, key=lambda n: counts[n]).index(k) for k in names}
        best = min(names, key=lambda k: (r_d[k] + r_c[k], density[k], counts[k]))
        print(f"Best route: {best}")
        summary = {k: f"{counts[k]} ppl, {density[k]:.1f}%" for k in counts}
        link.send(cmd="scan_result", summary=summary, best=best)

        # ---- 4. Siren on, start cart (it waits until the drone is ahead) ----
        path = dc.build_path(meta, best)
        L = dc.path_length(path)
        link.send(cmd="siren", on=True)
        await lead_cart(drone, path, L, 0.0, 0.0, home)

        print("Starting golf cart, escorting ...")
        link.send(cmd="cart_start", route=best, speed=CART_SPEED, lead=LEAD)
        await asyncio.sleep(0.3)

        # ---- 5. Escort ----------------------------------------------------
        t0 = time.time()
        while True:
            check_flying(veh, "escort")
            st = link.poll()
            mine = st.get("route") == best
            s_cart = st.get("cart_s", 0.0) if mine else 0.0
            await lead_cart(drone, path, L, s_cart, st.get("cart_v", 0.0) if mine else 0.0, home)
            if st.get("cart_done"):
                break
            if time.time() - t0 > 300:
                print("Escort timeout")
                break
            await asyncio.sleep(0.1)

        # ---- 6. Arrive: watch the rescue ----------------------------------
        print("Cart arrived at the incident. Medics are treating the patient ...")
        link.send(cmd="siren", on=False)
        x, y, h = dc.along(path, max(0.0, L - dc.CART_STOP_BEFORE - OBSERVE_BACK))
        n, e = dc.enu_to_ned(x, y, home)
        await goto(drone, n, e, dc.heading_to_yaw_ned_deg(h))
        t0 = time.time()
        while True:
            check_flying(veh, "rescue")
            st = link.poll()
            if st.get("ret_phase") in ("TURN", "RETURN", "DONE"):
                break
            if time.time() - t0 > RESCUE_TIMEOUT or not st.get("rescue"):
                print("No rescue sequence from sim_node (no medics in this world?) - skipping.")
                break
            await asyncio.sleep(0.2)

        # ---- 7. Escort back to the First Aid Center -----------------------
        if link.poll().get("ret_phase") in ("TURN", "RETURN", "DONE"):
            print("Patient on board. Escorting the cart back to the First Aid Center ...")
            rpath = dc.build_return_path(meta, best)
            RL = dc.path_length(rpath)
            link.send(cmd="siren", on=True)
            t0 = time.time()
            while True:
                check_flying(veh, "escort back")
                st = link.poll()
                await lead_cart(drone, rpath, RL, st.get("ret_s", 0.0), st.get("cart_v", 0.0), home)
                if st.get("ret_done"):
                    break
                if time.time() - t0 > 300:
                    print("Return escort timeout")
                    break
                await asyncio.sleep(0.1)
            link.send(cmd="siren", on=False)
            print("Patient delivered to the First Aid Center.")

        # ---- 8. Land on the golf cart -----------------------------------
        await land_on_cart(drone, veh, link, home)
        finished = True
    except Aborted as e:
        print(f"ABORTED: {e}. See the [PX4] / [MODE] lines above for the reason.")
    except asyncio.CancelledError:
        print("\nStopped by user.")
    finally:
        link.send(cmd="siren", on=False)
        if not finished:
            # Hover in place so the next run starts immediately from the air
            link.send(cmd="reset")
            try:
                await drone.offboard.stop()
            except Exception:  # noqa: BLE001
                pass
            if veh.armed:
                try:
                    await drone.action.hold()
                    print("Drone is holding position. Run mission.py again to start a new mission.")
                except Exception:  # noqa: BLE001
                    pass
    print("Mission finished.")


async def _run():
    task = asyncio.current_task()
    try:
        asyncio.get_running_loop().add_signal_handler(signal.SIGINT, task.cancel)
    except NotImplementedError:
        pass
    try:
        await main()
    except asyncio.CancelledError:
        pass


if __name__ == "__main__":
    asyncio.run(_run())
