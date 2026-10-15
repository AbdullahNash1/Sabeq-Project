"""SOURCE_CART=ros: world_node (sabeq_mission) /cart/pose, /cart/status, /mission_events.

- /cart/pose (10 Hz) -> cart.telemetry (position projected with §2).
- /cart/status (10 Hz) edges -> escorting -> arrived (state at_incident); rescue progress
  patient_loaded -> mission.rescue patient_loaded; at_hub with retPhase DONE -> the cart is at
  the First Aid Center (cart_at_hub; completed follows on the drone's landing).
- world_node events: patient_loaded -> the same hook; node_error -> error; command_ack ->
  RosContext. Speaker / LED toggles go out as /drone/payload_cmd (RosContext).
- Reset sim (POST /api/dev/sim/reset) also publishes the `reset` command to world_node.
"""
import logging

from app.ros import topics as T
from app.ros.converters import cart_pose, cart_status
from app.ros.wire import BEST_EFFORT_5, RELIABLE_10
from app.sources.base import CartSource
from app.sources.commands import MissionCommand
from app.state.machine import FAULTABLE_STATES, InvalidTransition, MissionState as S

log = logging.getLogger(__name__)


class RosCartSource(CartSource):

    def __init__(self, service, store, settings, ctx):
        super().__init__(service, store, settings)
        self.ctx = ctx
        ctx.register_part(self.part)
        self.status = None
        self.pose = None
        self._faults_reported: set = set()
        self._hub_reported = None        # mission id whose arrival at the hub was reported

    async def start(self) -> None:
        await self.ctx.start()
        self.ctx.subscribe(T.CART_POSE, "json", BEST_EFFORT_5, self._on_pose)
        self.ctx.subscribe(T.CART_STATUS, "json", RELIABLE_10, self._on_status)
        self.ctx.on_events_from("world_node", self._on_event)

    async def stop(self) -> None:
        await self.ctx.stop()

    def handle(self, cmd) -> None:
        self.ctx.publish_command(cmd)

    def reset(self) -> None:
        """Dev reset: put world_node's world back to its start too (no ack awaited)."""
        self._faults_reported.clear()
        self.ctx.publish_command(MissionCommand(None, "reset"))

    # --- telemetry -----------------------------------------------------------------------------------
    def _on_pose(self, msg: dict) -> None:
        try:
            self.pose = cart_pose(msg)
        except (KeyError, TypeError, ValueError) as exc:
            log.warning("bad /cart/pose: %s", exc)
            return
        self._telemetry()

    def _telemetry(self) -> None:
        pose = self.pose
        if pose is None:
            return
        state = self.status["state"] if self.status else self.store.cart.state
        self.service.cart_telemetry(state=state, position=self.service.local_position(pose["local"]),
                                    heading_deg=pose["heading_deg"], speed_kmh=pose["speed_kmh"])

    def _on_status(self, msg: dict) -> None:
        st = self.status = cart_status(msg)
        if st["state"] != self.store.cart.state:
            self._telemetry()
        mission = self.store.active_mission
        if mission is None or st["mission_id"] not in (None, mission.id):
            return
        try:
            if st["state"] == "at_incident" and mission.state is S.ESCORTING:
                self.service.cart_at_incident()
            elif (st["rescue_progress"] in ("patient_loaded", "delivered")
                  and mission.state is S.ARRIVED and mission.rescue is not None
                  and mission.rescue.state == "in_progress"):
                self.service.patient_loaded()
            elif (st["state"] == "at_hub" and st["ret_phase"] == "DONE"
                  and mission.state is S.RETURNING and self._hub_reported != mission.id):
                self._hub_reported = mission.id
                self.service.cart_at_hub()
        except InvalidTransition as exc:
            log.info("/cart/status edge ignored: %s", exc)

    # --- events ----------------------------------------------------------------------------------------
    def _on_event(self, event: dict) -> None:
        mission = self.store.active_mission
        if mission is None or event.get("missionId") not in (None, mission.id):
            return
        name, details = event.get("event"), event.get("details") or {}
        if name == "patient_loaded":
            if (mission.state is S.ARRIVED and mission.rescue is not None
                    and mission.rescue.state == "in_progress"):
                self.service.patient_loaded()
        elif name == "node_error":
            if mission.state in FAULTABLE_STATES and (mission.id, details.get("code")) not in \
                    self._faults_reported:
                self._faults_reported.add((mission.id, details.get("code")))
                self.service.fault(details.get("code", "node_error"),
                                   f"world_node: {details.get('message', '')}")
        else:
            log.debug("world_node event %s %s", name, details)
