"""Mock world_node: the golf cart and the medics, a port of docs/team/sim_node.py's World
and Rescue timing, in world ENU metres.

launch       -> once the drone is airborne, reset: the cart back to the start plaza
select_route -> to_incident: 10 m/s along the cart path, waiting for the drone to be
                9-12 m ahead, easing in over the last 12 m, stopping 5 m before the
                incident -> at_incident (cart_at_incident)
(arrived)    -> the medics walk to the patient, treat, load, walk back (patient_loaded)
start_return -> to_hub: U-turn (1.2 s), then the return path to the First Aid Center
                -> at_hub (cart_at_hub). The drone then lands on the cart.
abort        -> stopped in place. When that mission is closed, the operator's manual
                drive back (untracked) is simulated by snapping to the start plaza.
"""
import math

from app.sources.base import CartSource
from app.sources.commands import IncidentCommand, MissionCommand
from app.sources.mock.sim import (
    AIRBORNE_M,
    CART_SPEED_MPS,
    RETURN_SPEED_MPS,
    SLOW_MIN_MPS,
    SLOW_ZONE_M,
    TURN_TIME_S,
    Ticker,
    lead_factor,
    rescue_duration_s,
)
from app.state.machine import MissionState as S
from app.world import CART_STOP_BEFORE_M, along, compass_deg, path_length


class MockCartSource(CartSource):
    def __init__(self, service, store, settings):
        super().__init__(service, store, settings)
        self._ticker = Ticker(settings, "cart")
        self.reset()

    @property
    def world(self):
        return self.service.world

    def reset(self) -> None:
        self._to_start()
        self._reset_when_airborne = False
        self._pending: list = []

    def _to_start(self) -> None:
        self._pos = self.world.start
        self._heading = self.world.start_heading_rad  # ENU yaw
        self._state = "at_hub"
        self._phase = "idle"  # idle outbound rescue loaded turn return stopped
        self._route_id = None
        self._path, self._length, self._s = None, 0.0, 0.0
        self._rescue_left = 0.0
        self._turn_t, self._turn_h0, self._turn_h1 = 0.0, 0.0, 0.0
        self._speed = 0.0

    async def start(self) -> None:
        self._report()
        self._ticker.start(self._step)

    async def stop(self) -> None:
        await self._ticker.stop()

    def handle(self, cmd) -> None:
        if isinstance(cmd, MissionCommand) and cmd.command in ("launch", "select_route", "start_return", "abort"):
            self._pending.append((cmd.command, cmd.route))
        elif isinstance(cmd, IncidentCommand) and not cmd.active:
            self._pending.append(("closed", None))

    # --- tick ---------------------------------------------------------------------

    def _step(self, dt: float) -> None:
        for command, route in self._pending:
            self._apply(command, route)
        self._pending.clear()

        if self._reset_when_airborne and self.store.drone.altitude_m >= AIRBORNE_M:
            self._to_start()  # mission.py: reset the world only once the drone is in the air
            self._reset_when_airborne = False

        self._speed = 0.0
        phase = self._phase
        if phase == "outbound":
            self._drive_outbound(dt)
        elif phase == "rescue":
            self._rescue(dt)
        elif phase == "turn":
            self._turn(dt)
        elif phase == "return":
            self._drive_return(dt)
        self._report()

    def _apply(self, command: str, route) -> None:
        if command == "launch":
            self._reset_when_airborne = True
        elif command == "select_route" and route in self.world.routes:
            self._reset_when_airborne = False
            self._route_id = route
            self._path = self.world.cart_path(route)
            self._length, self._s = path_length(self._path), 0.0
            self._phase, self._state = "outbound", "to_incident"
        elif command == "start_return" and self._route_id is not None:
            self._path = self.world.return_path(self._route_id)
            self._length, self._s = path_length(self._path), 0.0
            self._turn_t, self._turn_h0 = 0.0, self._heading
            self._turn_h1 = along(self._path, 0.0)[2]
            self._phase, self._state = "turn", "to_hub"
        elif command == "abort":
            self._phase, self._state = "stopped", "stopped"
        elif command == "closed" and self._state == "stopped":
            self._to_start()  # simulated manual drive back

    # --- phases ---------------------------------------------------------------------

    def _drive_outbound(self, dt: float) -> None:
        end = self._length - CART_STOP_BEFORE_M
        v = CART_SPEED_MPS
        if end - self._s < SLOW_ZONE_M:
            v = max(SLOW_MIN_MPS, min(CART_SPEED_MPS, 0.6 * (end - self._s)))
        v *= lead_factor(self._path, self._length, self._s, self._drone_local())
        self._advance(v, dt, end)
        if self._s >= end:
            self._phase, self._state = "rescue", "at_incident"
            self._rescue_left = rescue_duration_s(self._pos, self._heading, self.world.patient)
            self._report()
            mission = self.store.active_mission
            if mission is not None and mission.state is S.ESCORTING:
                self.service.cart_at_incident()

    def _rescue(self, dt: float) -> None:
        mission = self.store.active_mission
        if mission is None or mission.state is not S.ARRIVED:
            return  # the clock starts once the backend has registered the arrival
        self._rescue_left -= dt
        if self._rescue_left <= 0:
            self._phase = "loaded"  # wait for start_return with the patient aboard
            self.service.patient_loaded()

    def _turn(self, dt: float) -> None:
        self._turn_t += dt
        f = min(1.0, self._turn_t / TURN_TIME_S)
        diff = (self._turn_h1 - self._turn_h0 + math.pi) % (2 * math.pi) - math.pi
        self._heading = self._turn_h0 + diff * f
        if f >= 1.0:
            self._phase = "return"

    def _drive_return(self, dt: float) -> None:
        v = RETURN_SPEED_MPS * lead_factor(self._path, self._length, self._s, self._drone_local())
        self._advance(v, dt, self._length)
        if self._s >= self._length:
            self._phase, self._state = "idle", "at_hub"  # at the First Aid Center
            self._report()
            self.service.cart_at_hub()

    # --- helpers --------------------------------------------------------------------

    def _advance(self, v: float, dt: float, end: float) -> None:
        self._s = min(self._s + v * dt, end)
        e, n, h = along(self._path, self._s)
        self._pos, self._heading, self._speed = (e, n), h, v

    def _drone_local(self):
        """The drone's position for the lead factor; None while it sits on the cart."""
        drone = self.store.drone
        if drone.docked_on_cart:
            return None
        return self.service.projection.position_to_local(drone.position)

    def _report(self) -> None:
        self.service.cart_telemetry(
            state=self._state,
            position=self.service.local_position(self._pos),
            heading_deg=round(compass_deg(self._heading), 1),
            speed_kmh=round(self._speed * 3.6, 1),
        )
