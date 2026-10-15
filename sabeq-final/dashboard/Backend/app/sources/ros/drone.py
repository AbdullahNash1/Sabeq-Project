"""SOURCE_DRONE=ros: mission_node (sabeq_mission) /drone/status, /drone/position, /mission_events.

- /drone/status (1-2 Hz) + /drone/position (10 Hz) -> drone.telemetry (position projected with
  §2), scan progress, payload confirmation, faults -> error.
- mission_node events: scan_altitude_reached -> taking_off -> scanning; landed_on_dock ->
  returning -> completed; battery_low -> abort; node_error -> error. command_ack -> RosContext.
- Heartbeat: no /drone/status for HEARTBEAT_TIMEOUT_S -> ROS chip red, link lost, and an
  active mission goes to error (link_lost).
- Commands from the CommandBus are published by RosContext (once, whichever part gets them).
"""
import asyncio
import logging
import time

from app.ros import topics as T
from app.ros.converters import drone_position, drone_status
from app.ros.wire import BEST_EFFORT_5, RELIABLE_10
from app.sources.base import DroneSource
from app.state.machine import FAULTABLE_STATES, InvalidTransition, MissionState as S

log = logging.getLogger(__name__)


class RosDroneSource(DroneSource):

    def __init__(self, service, store, settings, ctx):
        super().__init__(service, store, settings)
        self.ctx = ctx
        ctx.register_part(self.part)
        self.status = None
        self.position = None
        self.status_wall = None
        self._lost_reported = False
        self._faults_reported: set = set()
        self._task = None

    async def start(self) -> None:
        await self.ctx.start()
        self.ctx.subscribe(T.DRONE_STATUS, "json", RELIABLE_10, self._on_status)
        self.ctx.subscribe(T.DRONE_POSITION, "json", BEST_EFFORT_5, self._on_position)
        self.ctx.on_events_from("mission_node", self._on_event)
        self._task = asyncio.get_running_loop().create_task(self._watch())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        await self.ctx.stop()

    def handle(self, cmd) -> None:
        self.ctx.publish_command(cmd)

    def reset(self) -> None:
        self._faults_reported.clear()
        self._lost_reported = False

    # --- telemetry -------------------------------------------------------------------------------
    def _on_position(self, msg: dict) -> None:
        try:
            self.position = drone_position(msg)
        except (KeyError, TypeError, ValueError) as exc:
            log.warning("bad /drone/position: %s", exc)
            return
        self._telemetry()

    def _on_status(self, msg: dict) -> None:
        self.status = drone_status(msg)
        self.status_wall = time.monotonic()
        self._lost_reported = False
        self.service.ros_status(True, "OK")
        self._telemetry()
        st = self.status
        mission = self.store.active_mission
        mine = mission is not None and st["mission_id"] in (None, mission.id)
        if mission is not None and st["payload"] is not None and mine:
            speaker, led = st["payload"]
            if (mission.payload.speaker, mission.payload.led) != (speaker, led):
                self.service.payload_confirmed(speaker, led)
        if mission is not None and st["scan"] and st["mission_id"] == mission.id:
            scan = st["scan"]
            pos = self.position or {}
            self.service.scan_progress(scan.get("phase", "climb"), float(scan.get("progress") or 0),
                                       pos.get("altitude_m", 0.0), pos.get("heading_deg", 0.0),
                                       float(scan.get("coveragePct") or 0))
        if st["fault"] and mission is not None and st["mission_id"] == mission.id:
            self._fault(st["fault"].get("code", "drone_fault"), st["fault"].get("message", ""))

    def _telemetry(self) -> None:
        st, pos = self.status, self.position
        if st is None:
            return
        drone = self.store.drone
        position = (self.service.local_position(pos["local"]) if pos is not None
                    else drone.position)
        battery = st["battery_pct"] if st["battery_pct"] is not None else drone.battery_pct
        self.service.drone_telemetry(
            state=st["state"], battery_pct=battery, position=position,
            altitude_m=pos["altitude_m"] if pos else drone.altitude_m,
            heading_deg=pos["heading_deg"] if pos else drone.heading_deg,
            speed_mps=pos["speed_mps"] if pos else 0.0, docked_on_cart=st["docked"],
            link=st["link"], flight_time_left_s=st["flight_time_left_s"],
            camera_mode=st["camera_mode"])

    # --- events ------------------------------------------------------------------------------------
    def _on_event(self, event: dict) -> None:
        mission = self.store.active_mission
        if mission is None or event.get("missionId") not in (None, mission.id):
            return
        name, details = event.get("event"), event.get("details") or {}
        try:
            if name == "scan_altitude_reached" and mission.state is S.TAKING_OFF:
                self.service.scan_altitude_reached()
            elif name == "landed_on_dock":
                self.service.drone_landed()
            elif name == "battery_low":
                self.service.battery_low()
            elif name == "node_error":
                self._fault(details.get("code", "node_error"), details.get("message", ""))
            else:
                log.debug("mission_node event %s %s", name, details)
        except InvalidTransition as exc:
            log.info("mission_node %s ignored: %s", name, exc)

    def _fault(self, code: str, message: str) -> None:
        mission = self.store.active_mission
        if mission is None or mission.state not in FAULTABLE_STATES:
            return
        key = (mission.id, code)
        if key in self._faults_reported:
            return
        self._faults_reported.add(key)
        self.service.fault(code, f"mission_node: {message}" if message else "mission_node")

    # --- heartbeat --------------------------------------------------------------------------------
    async def _watch(self) -> None:
        while True:
            await asyncio.sleep(1.0)
            if self.status_wall is None:
                continue
            silent = time.monotonic() - self.status_wall > self.settings.heartbeat_timeout_s
            if silent and not self._lost_reported:
                self._lost_reported = True
                self.service.ros_status(False, "—")
                drone = self.store.drone
                self.service.drone_telemetry(
                    state="offline", battery_pct=drone.battery_pct, position=drone.position,
                    altitude_m=drone.altitude_m, heading_deg=drone.heading_deg, speed_mps=0.0,
                    docked_on_cart=drone.docked_on_cart, link="lost")
                self._fault("link_lost", f"no /drone/status for "
                                         f"{self.settings.heartbeat_timeout_s:g} s")
