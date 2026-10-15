"""Mock mission_node (Node 3): a port of docs/team/mission.py's flight, in world ENU metres.

launch       -> take off from the cart roof to the scan altitude (8 m) and fly to the
                scan point above the start plaza -> scan_altitude_reached
scan         -> per route, in scan order: yaw to its scan yaw (world_meta.json), settle,
                hold the heading -> scan done
select_route -> escort 15 m ahead of the cart along the cart path
(arrived)    -> hover 8 m behind the stopped cart and watch the rescue
start_return -> escort 15 m ahead along the return path to the First Aid Center
(cart at hub)-> come over the cart, descend to 1.8 m, land -> drone_landed()
abort        -> fly to the cart and land on it
MOCK_LOW_BATTERY -> battery_low() once, halfway along the outbound leg.
"""
import math

from app.sources.base import DroneSource
from app.sources.commands import MissionCommand, PayloadCommand
from app.sources.mock.sim import (
    AIRBORNE_M,
    CHARGE_PCT_PER_S,
    DESCENT_MPS,
    DRAIN_PCT_PER_S,
    DRONE_MAX_MPS,
    FULL_FLIGHT_S,
    LAND_MPS,
    LEAD_M,
    OBSERVE_BACK_M,
    OVER_CART_M,
    ROOF_HOVER_M,
    SCAN_HOLD_S,
    SCAN_POINT_TOL_M,
    SCAN_SETTLE_S,
    SCAN_YAW_TOL_DEG,
    TAKEOFF_MPS,
    YAW_DPS,
    Ticker,
    move_towards,
)
from app.state.machine import MissionState as S
from app.world import CART_STOP_BEFORE_M, along, angle_diff_deg, compass_deg, path_length, project

SCAN_CLIMB_SHARE = 0.3  # scan.progress 0..0.3 = take-off, 0.3..1 = the route headings


class MockDroneSource(DroneSource):
    def __init__(self, service, store, settings):
        super().__init__(service, store, settings)
        self._ticker = Ticker(settings, "drone")
        self.reset()

    @property
    def world(self):
        return self.service.world

    def reset(self) -> None:
        # docked climb scan scan_done escort hover to_cart landing
        self._phase = "docked"
        self._aborting = False
        self._leg = None
        self._route_id = None
        self._pos = self._cart_local()
        self._alt = 0.0
        self._heading = self.store.cart.heading_deg
        self._speed = 0.0
        self._scan_idx = 0
        self._scan_step = "turn"  # turn settle hold
        self._scan_t = 0.0
        self._battery = 100.0
        self._low_battery_sent = False
        self._pending: list = []

    async def start(self) -> None:
        self._report()
        self._ticker.start(self._step)

    async def stop(self) -> None:
        await self._ticker.stop()

    def handle(self, cmd) -> None:
        if isinstance(cmd, (MissionCommand, PayloadCommand)):
            self._pending.append(cmd)

    # --- tick ---------------------------------------------------------------------

    def _step(self, dt: float) -> None:
        for cmd in self._pending:
            self._apply(cmd)
        self._pending.clear()

        mission = self.store.active_mission
        self._speed = 0.0
        phase = self._phase
        if phase == "docked":
            self._pos = self._cart_local()
            self._heading = self.store.cart.heading_deg
            self._battery = min(100.0, self._battery + CHARGE_PCT_PER_S * dt)
        elif phase == "climb":
            self._climb(dt, mission)
        elif phase == "scan":
            self._scan(dt, mission)
        elif phase == "escort":
            self._escort(dt, mission)
        elif phase == "hover":
            self._hover(dt, mission)
        elif phase == "to_cart":
            self._to_cart(dt)
        elif phase == "landing":
            self._land(dt, mission)

        if self._phase != "docked":
            self._battery = max(0.0, self._battery - DRAIN_PCT_PER_S * dt)
        self._report()

    def _apply(self, cmd) -> None:
        if isinstance(cmd, PayloadCommand):
            payload = self.store.active_mission.payload if self.store.active_mission else None
            if payload is not None:
                self.service.payload_confirmed(
                    speaker=payload.speaker if cmd.speaker is None else cmd.speaker,
                    led=payload.led if cmd.led is None else cmd.led,
                )
            return
        if cmd.command == "launch" and self._phase == "docked":
            self._phase, self._alt, self._aborting = "climb", 0.0, False
            self._scan_idx, self._scan_step, self._scan_t = 0, "turn", 0.0
            self._low_battery_sent = False
        elif cmd.command == "select_route":
            self._phase, self._leg, self._route_id = "escort", "outbound", cmd.route
        elif cmd.command == "start_return":
            self._phase, self._leg = "escort", "return"
        elif cmd.command == "abort" and self._phase != "docked":
            self._phase, self._leg, self._aborting = "to_cart", None, True

    # --- phases ---------------------------------------------------------------------

    def _climb(self, dt, mission) -> None:
        target_alt = self.settings.scan_altitude_m
        self._alt = min(target_alt, self._alt + TAKEOFF_MPS * dt)
        if self._alt >= AIRBORNE_M:  # mission.py flies to the scan point once in the air
            self._fly(self.world.hover_point, dt)
        if mission is None:
            return
        self.service.scan_progress("climb", SCAN_CLIMB_SHARE * self._alt / target_alt, self._alt,
                                   self._heading, 0.0)
        at_point = math.dist(self._pos, self.world.hover_point) <= SCAN_POINT_TOL_M
        if self._alt >= target_alt and at_point:
            self._phase = "scan"
            if mission.state is S.TAKING_OFF:
                self.service.scan_altitude_reached()

    def _scan(self, dt, mission) -> None:
        routes = list(self.world.routes.values())
        route = routes[self._scan_idx]
        target = route.scan_yaw_ned_deg
        if self._scan_step == "turn":
            self._turn_towards(target, dt)
            if angle_diff_deg(self._heading, target) <= SCAN_YAW_TOL_DEG:
                self._scan_step, self._scan_t = "settle", 0.0
        else:
            self._turn_towards(target, dt)
            self._scan_t += dt
            if self._scan_step == "settle" and self._scan_t >= SCAN_SETTLE_S:
                self._scan_step, self._scan_t = "hold", 0.0
            elif self._scan_step == "hold" and self._scan_t >= SCAN_HOLD_S:
                self._scan_idx += 1
                self._scan_step, self._scan_t = "turn", 0.0
        if mission is None:
            return
        if self._scan_idx >= len(routes):
            self._phase = "scan_done"
            self.service.scan_progress("done", 1.0, self._alt, self._heading, 100.0)
            return
        held = min(1.0, self._scan_t / SCAN_HOLD_S) if self._scan_step == "hold" else 0.0
        frac = (self._scan_idx + held) / len(routes)
        self.service.scan_progress("rotate", SCAN_CLIMB_SHARE + (1 - SCAN_CLIMB_SHARE) * frac,
                                   self._alt, self._heading, 100.0 * frac)

    def _leg_path(self):
        if self._route_id not in self.world.routes:
            return None
        if self._leg == "return":
            return self.world.return_path(self._route_id)
        return self.world.cart_path(self._route_id)

    def _escort(self, dt, mission) -> None:
        if mission is not None and mission.state is S.ARRIVED:
            self._phase = "hover"
            return
        if self._leg == "return" and self.store.cart.state == "at_hub":
            self._phase = "to_cart"  # cart at the First Aid Center: land on it
            return
        path = self._leg_path()
        if path is None:
            return
        length = path_length(path)
        cart_s = project(path, self._cart_local())[0]
        e, n, h = along(path, min(cart_s + LEAD_M, length))
        self._fly((e, n), dt)
        self._heading = round(compass_deg(h), 1)
        self._alt = self._approach(self._alt, self.settings.scan_altitude_m, TAKEOFF_MPS * dt)
        if (self.settings.mock_low_battery and not self._low_battery_sent and self._leg == "outbound"
                and mission is not None and mission.state is S.ESCORTING and cart_s >= 0.5 * length):
            self._low_battery_sent = True
            self._battery = min(self._battery, 18.0)
            self.service.battery_low()

    def _hover(self, dt, mission) -> None:
        if mission is None or mission.state is not S.ARRIVED:
            return
        path = self.world.cart_path(self._route_id)
        stop = path_length(path) - CART_STOP_BEFORE_M
        e, n, h = along(path, max(0.0, stop - OBSERVE_BACK_M))
        self._fly((e, n), dt)
        self._heading = round(compass_deg(h), 1)

    def _to_cart(self, dt) -> None:
        cart = self._cart_local()
        if math.dist(self._pos, cart) > OVER_CART_M:  # come over the cart at escort height first
            self._fly(cart, dt)
            return
        self._fly(cart, dt)
        self._heading = self.store.cart.heading_deg
        self._alt = self._approach(self._alt, ROOF_HOVER_M, DESCENT_MPS * dt)
        if self._alt <= ROOF_HOVER_M and math.dist(self._pos, cart) < 0.25:
            self._phase = "landing"

    def _land(self, dt, mission) -> None:
        self._pos = self._cart_local()
        self._alt = max(0.0, self._alt - LAND_MPS * dt)
        if self._alt > 0.0:
            return
        self._phase, self._leg, self._route_id = "docked", None, None
        self._report()
        if mission is not None and mission.state is S.RETURNING:
            self.service.drone_landed()

    # --- helpers --------------------------------------------------------------------

    def _cart_local(self) -> tuple:
        position = self.store.cart.position
        local = self.service.projection.position_to_local(position)
        return tuple(local) if local is not None else (position.x, position.y)

    def _fly(self, target, dt) -> None:
        self._pos, moved = move_towards(self._pos, target, DRONE_MAX_MPS * dt)
        self._speed = moved / dt if dt else 0.0

    def _turn_towards(self, target_deg: float, dt: float) -> None:
        diff = (target_deg - self._heading + 180.0) % 360.0 - 180.0
        step = YAW_DPS * dt
        self._heading = target_deg % 360.0 if abs(diff) <= step else (self._heading + math.copysign(step, diff)) % 360.0

    @staticmethod
    def _approach(value, target, step) -> float:
        return min(target, value + step) if value < target else max(target, value - step)

    def _state(self) -> str:
        if self._phase == "to_cart":
            return "returning_to_dock" if self._aborting else "landing"
        return {
            "docked": "docked",
            "climb": "taking_off",
            "scan": "scanning",
            "scan_done": "scanning",
            "escort": "escorting",
            "hover": "escorting",
            "landing": "landing",
        }[self._phase]

    def _camera_mode(self) -> str:
        if self._phase == "docked":
            return "off"
        return "scan_360" if self._phase in ("scan", "scan_done") else "forward"

    def _report(self) -> None:
        self.service.drone_telemetry(
            state=self._state(),
            battery_pct=round(self._battery, 1),
            flight_time_left_s=round(FULL_FLIGHT_S * self._battery / 100.0),
            position=self.service.local_position(self._pos),
            altitude_m=round(self._alt, 1),
            heading_deg=round(self._heading, 1),
            speed_mps=round(self._speed, 1),
            docked_on_cart=self._phase == "docked",
            link="ok",
            camera_mode=self._camera_mode(),
        )
