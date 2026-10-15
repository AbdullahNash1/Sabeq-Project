"""Mission service: the only place that changes state (API_CONTRACT §4.4, §6.1).

Each operation: check the state machine -> mutate the store -> publish typed
commands on the CommandBus -> broadcast WS messages. REST routers call the
command methods; data sources (mock or ROS, the service can't tell) call the
hooks. Everything runs on the event loop thread.
"""
import asyncio
import logging
import math
import time
from datetime import datetime, timedelta, timezone
from typing import Callable

from app.broadcaster import Broadcaster
from app.config import Settings
from app.errors import ApiError
from app.geo import Projection
from app.models import (
    CameraDetections,
    CreateIncident,
    EscortInfo,
    ImageSize,
    Incident,
    IncidentLocation,
    IncidentType,
    Mission,
    MissionError,
    MissionResult,
    MissionSummary,
    PayloadRequest,
    PayloadState,
    Position,
    RescueInfo,
    Route,
    ScanInfo,
    SystemStatus,
    TimelineEntry,
)
from app.models.ws import (
    CartTelemetry,
    DroneTelemetry,
    EscortProgress,
    IncidentEvent,
    MissionHistoryEvent,
    MissionStateEvent,
    PayloadStateEvent,
    RouteRecommendation,
    RouteScores,
    RouteSelected,
    ScanProgress,
    WsError,
)
from app.sources.bus import CommandBus
from app.sources.commands import (
    CommandFailed,
    CommandRejected,
    CommandTimeout,
    IncidentCommand,
    MissionCommand,
    PayloadCommand,
)
from app.state.machine import (
    ABORTABLE_STATES,
    Command,
    MissionState as S,
    InvalidTransition,
    Trigger,
    ensure_command_allowed,
    resolve_close_outcome,
    transition,
)
from app.state.store import MissionRuntime, Store, create_default_store
from app.world import World, path_length, project

log = logging.getLogger(__name__)

# Broadcast rate limits (§5.2), in real seconds.
TELEMETRY_INTERVAL_S = 0.2  # drone/cart telemetry, scan.progress: <= 5 Hz
ESCORT_INTERVAL_S = 0.5  # escort.progress: 2 Hz


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MissionService:
    def __init__(
        self,
        store: Store,
        settings: Settings,
        broadcaster: Broadcaster,
        bus: CommandBus,
        projection: Projection,
        world: World,
        clock: Callable[[], datetime] = utcnow,
    ):
        self.store = store
        self.settings = settings
        self.broadcaster = broadcaster
        self.bus = bus
        self.projection = projection
        self.world = world
        self.now = clock
        self._last_sent: dict = {}  # throttle key -> monotonic time
        self._tasks: set = set()     # background command sends (keep references)
        self._scan_phase_sent: tuple = ()  # (mission id, phase) last broadcast in scan.progress

    # ======================================================================
    # Reads
    # ======================================================================

    def system_status(self) -> SystemStatus:
        backend = self.store.system.backend
        backend.uptime_s = round((self.now() - self.store.started_at).total_seconds(), 1)
        return self.store.system

    def get_mission(self, mission_id: str) -> Mission:
        mission = self.store.find_mission(mission_id)
        if mission is None:
            raise ApiError(404, "not_found", f"Mission '{mission_id}' not found")
        return mission

    def get_incident(self, incident_id: str) -> Incident:
        incident = self.store.find_incident(incident_id)
        if incident is None:
            raise ApiError(404, "not_found", f"Incident '{incident_id}' not found")
        return incident

    # ======================================================================
    # Incidents
    # ======================================================================

    def create_incident(self, body: CreateIncident) -> Incident:
        # The world has one fixed incident (docs/WORLD.md §2): every incident is placed there,
        # whatever coordinates were posted. Zone, description, type and severity are kept.
        loc = body.location
        position = self.local_position(self.world.incident)
        incident = Incident(
            id=self.store.next_incident_id(),
            status="queued",
            severity=body.severity,
            type=IncidentType(code=body.type, label=body.title),
            location=IncidentLocation(zone=loc.zone, description=loc.description, position=position),
            source=body.source,
            reported_at=body.reported_at or self.now(),
            distance_from_hub_m=round(math.dist(self.world.first_aid_door, self.world.incident), 1),
        )
        if self.store.active_mission is None:
            self._activate(incident, created=True)
        else:
            self.store.enqueue_incident(incident)
            self._emit_incident("incident.created", incident)
        return incident

    def _activate(self, incident: Incident, created: bool) -> None:
        """idle -> incident_received for `incident` (new, or popped from the queue)."""
        now = self.now()
        mission = Mission(
            id=self.store.next_mission_id(),
            incident_id=incident.id,
            drone_id=self.settings.drone_id,
            cart_id=self.settings.cart_id,
            state=S.IDLE,
            state_entered_at=now,
            scan_altitude_m=self.settings.scan_altitude_m,
            scan=ScanInfo(),
            routes=[],
            escort=EscortInfo(),
            payload=PayloadState(speaker=False, led=False),
            timeline=[],
            created_at=now,
        )
        from_state = self._transition(mission, Trigger.INCIDENT_ACTIVATED)
        incident.status = "active"
        incident.mission_id = mission.id
        self.store.active_incident = incident
        self.store.active_mission = mission
        self.store.runtime = MissionRuntime()

        position = incident.location.position
        self.bus.publish(IncidentCommand(
            active=True,
            mission_id=mission.id,
            incident_id=incident.id,
            severity=incident.severity,
            type=incident.type.code,
            zone=incident.location.zone,
            position=position,
            local_m=self.world.incident,
            scan_altitude_m=mission.scan_altitude_m,
        ))
        self._emit_incident("incident.created" if created else "incident.updated", incident)
        self._emit_state(mission, from_state)

    # ======================================================================
    # Commands (REST §4.4)
    # ======================================================================

    async def launch(self, mission_id: str) -> Mission:
        mission = self._command(mission_id, Command.LAUNCH)
        drone = self.store.drone
        if not (drone.state == "docked" and drone.docked_on_cart and drone.link != "lost"):
            raise ApiError(
                503, "drone_unavailable",
                f"Drone {drone.id} is not ready (state={drone.state}, dockedOnCart="
                f"{drone.docked_on_cart}, link={drone.link})",
            )
        await self._send(MissionCommand(mission.id, "launch", scan_altitude_m=mission.scan_altitude_m))
        mission = self._command(mission_id, Command.LAUNCH)  # still allowed after the acks
        from_state = self._transition(mission, Trigger.LAUNCH)
        mission.scan.phase = "climb"
        self._emit_state(mission, from_state)
        return mission

    def dismiss(self, mission_id: str) -> Mission:
        mission = self._command(mission_id, Command.DISMISS)
        from_state = self._transition(mission, Trigger.DISMISS)
        self._close_out(mission, "cancelled", from_state)
        return mission

    async def select_route(self, mission_id: str, route: str) -> Mission:
        mission = self._command(mission_id, Command.ROUTE)
        ids = [r.id for r in mission.routes]
        if route not in ids:
            raise ApiError(400, "bad_request", f"route '{route}' is not one of {ids}")
        if mission.state is S.SCANNING:
            mission.preselected_route = route  # locks when the recommendation arrives
            self._emit_state(mission, mission.state, reason="preselected")
            return mission
        self._cancel_timer()  # the operator overrides: the countdown must not fire meanwhile
        await self._send(self._select_route_command(mission, route, "operator"))
        mission = self._command(mission_id, Command.ROUTE)
        if mission.state is not S.ROUTE_SELECTED:
            raise InvalidTransition(mission.state, Command.ROUTE.value)
        self._commit_lock(mission, route, "operator")
        return mission

    def set_payload(self, mission_id: str, body: PayloadRequest) -> PayloadState:
        mission = self._command(mission_id, Command.PAYLOAD)
        if body.speaker is not None:
            mission.payload.speaker = body.speaker
        if body.led is not None:
            mission.payload.led = body.led
        self.bus.publish(PayloadCommand(mission.id, speaker=body.speaker, led=body.led))
        self._emit_payload(mission)
        return mission.payload

    async def abort(self, mission_id: str, reason: str) -> Mission:
        mission = self._command(mission_id, Command.ABORT)
        await self._send(MissionCommand(mission.id, "abort", reason=reason))
        mission = self._command(mission_id, Command.ABORT)
        self._commit_abort(mission, reason)
        return mission

    async def start_return(self, mission_id: str) -> Mission:
        mission = self._command(mission_id, Command.RETURN)
        self._check_patient_loaded(mission)
        await self._send(MissionCommand(
            mission.id, "start_return", route=mission.selected_route,
            polyline_local=self._leg_local(mission, mission.selected_route, "return"),
        ))
        mission = self._command(mission_id, Command.RETURN)
        self._check_patient_loaded(mission)
        from_state = self._transition(mission, Trigger.START_RETURN)
        mission.escort = EscortInfo(leg="return", eta_s=self._route(mission, mission.selected_route).est_time_s)
        self._emit_state(mission, from_state)
        return mission

    @staticmethod
    def _check_patient_loaded(mission: Mission) -> None:
        if mission.rescue is None or mission.rescue.state != "patient_loaded":
            raise ApiError(409, "invalid_transition", "Cannot return before the patient is loaded", {
                "state": mission.state.value, "action": Command.RETURN.value,
                "rescue": mission.rescue.state if mission.rescue else None,
            })

    def close(self, mission_id: str, outcome: "str | None") -> Mission:
        mission = self.get_mission(mission_id)
        final = resolve_close_outcome(mission.state, outcome)  # 409 / 400
        from_state = self._transition(mission, Trigger.CLOSE)
        self._close_out(mission, final, from_state)
        return mission

    # ======================================================================
    # Hooks: mission milestones (state transitions). Raise InvalidTransition
    # if the mission is not in the expected state.
    # ======================================================================

    def scan_altitude_reached(self) -> Mission:
        mission = self._active()
        from_state = self._transition(mission, Trigger.SCAN_ALTITUDE_REACHED)
        mission.scan.phase = "rotate"
        self._emit_state(mission, from_state)
        return mission

    def route_recommended(self, routes: list, recommended: str) -> Mission:
        """Node 2's pick (after its final scores): scanning -> route_selected."""
        mission = self._active()
        ids = [r.id for r in routes]
        if recommended not in ids:
            raise ValueError(f"recommended route '{recommended}' is not one of {ids}")
        from_state = self._transition(mission, Trigger.ROUTE_RECOMMENDED)
        mission.routes = with_scores([r.model_copy(update={"count_final": True}) for r in routes])
        mission.recommended_route = recommended
        mission.scan = ScanInfo(phase="done", progress=1, coverage_pct=100)
        runtime = self.store.runtime
        if mission.preselected_route not in ids:
            mission.preselected_route = None

        if mission.preselected_route is not None:
            self._emit_state(mission, from_state)
            self._lock_route_in_background(mission, mission.preselected_route, "operator")
            return mission

        seconds = self.settings.auto_select_seconds
        mission.auto_select_at = self.now() + timedelta(seconds=seconds)
        self._emit_state(mission, from_state)
        self.broadcaster.publish("route.recommendation", RouteRecommendation(
            mission_id=mission.id, recommended=recommended, routes=mission.routes,
            auto_select_at=mission.auto_select_at, countdown_s=seconds,
        ))
        runtime.auto_select_handle = asyncio.get_running_loop().call_later(
            seconds, self._auto_select_fire, mission.id
        )
        return mission

    def cart_at_incident(self) -> Mission:
        mission = self._active()
        from_state = self._transition(mission, Trigger.CART_AT_INCIDENT)
        mission.escort.progress = 1
        mission.escort.eta_s = 0
        mission.result = self._result(mission)
        mission.rescue = RescueInfo(state="in_progress")  # medics leave the cart for the patient
        self._emit_state(mission, from_state)
        return mission

    def patient_loaded(self) -> Mission:
        """Medics are back on the cart with the patient: Start return becomes allowed."""
        mission = self._active()
        if mission.state is not S.ARRIVED or mission.rescue is None:
            raise InvalidTransition(mission.state, "patient_loaded")
        if mission.rescue.state != "patient_loaded":
            mission.rescue = RescueInfo(state="patient_loaded", patient_loaded_at=self.now())
            self._emit_state(mission, mission.state, reason="patient_loaded")
        return mission

    def cart_at_hub(self) -> "Mission | None":
        """Cart stopped at the First Aid Center. No transition: the mission completes when
        the drone has landed on the cart. Ignored outside returning."""
        mission = self.store.active_mission
        if mission is None or mission.state is not S.RETURNING:
            return None
        mission.escort.progress = 1
        mission.escort.eta_s = 0
        self._emit_state(mission, mission.state, reason="cart_at_hub")
        return mission

    def drone_landed(self) -> "Mission | None":
        """Drone landed on the cart roof: returning -> completed. Ignored in any other
        state (e.g. the landing after an abort)."""
        mission = self.store.active_mission
        if mission is None or mission.state is not S.RETURNING:
            return None
        from_state = self._transition(mission, Trigger.DRONE_LANDED)
        self._emit_state(mission, from_state)
        return mission

    def fault(self, code: str, message: str) -> Mission:
        mission = self._active()
        from_state = self._transition(mission, Trigger.FAULT)
        self._cancel_timer()
        mission.error = MissionError(code=code, message=message)
        self._emit_state(mission, from_state, reason=code)
        self.broadcaster.publish("error", WsError(code=code, message=message, mission_id=mission.id))
        return mission

    def battery_low(self) -> "Mission | None":
        mission = self.store.active_mission
        if mission is None or mission.state not in ABORTABLE_STATES:
            return None
        mission_id = mission.id

        def commit():
            m = self.store.active_mission
            if m is not None and m.id == mission_id and m.state in ABORTABLE_STATES:
                self._commit_abort(m, "low_battery")
        self._send_in_background(MissionCommand(mission.id, "abort", reason="low_battery"),
                                 commit, mission_id)
        return mission

    # ======================================================================
    # Hooks: telemetry (never raise for a missing mission; just update + broadcast)
    # ======================================================================

    def drone_telemetry(self, *, state: str, battery_pct: float, position: Position, altitude_m: float,
                        heading_deg: float, speed_mps: float, docked_on_cart: bool, link: str,
                        flight_time_left_s: "float | None" = None, camera_mode: "str | None" = None) -> None:
        drone = self.store.drone
        state_changed = drone.state != state or drone.docked_on_cart != docked_on_cart or drone.link != link
        drone.state, drone.battery_pct, drone.position = state, battery_pct, position
        drone.altitude_m, drone.heading_deg, drone.speed_mps = altitude_m, heading_deg, speed_mps
        drone.docked_on_cart, drone.link = docked_on_cart, link
        if flight_time_left_s is not None:
            drone.flight_time_left_s = flight_time_left_s
        if camera_mode is not None and camera_mode != self.store.camera.mode:
            self.store.camera.mode = camera_mode
            self.broadcaster.publish("camera.info", self.store.camera)
        if state_changed or self._due("drone.telemetry", TELEMETRY_INTERVAL_S):
            self.broadcaster.publish("drone.telemetry", DroneTelemetry(
                drone_id=drone.id, state=drone.state, battery_pct=drone.battery_pct,
                flight_time_left_s=drone.flight_time_left_s, position=drone.position,
                altitude_m=drone.altitude_m, heading_deg=drone.heading_deg, speed_mps=drone.speed_mps,
                docked_on_cart=drone.docked_on_cart, link=drone.link,
            ))

    def cart_telemetry(self, *, state: str, position: Position, heading_deg: float, speed_kmh: float) -> None:
        cart = self.store.cart
        state_changed = cart.state != state
        cart.state, cart.position, cart.heading_deg, cart.speed_kmh = state, position, heading_deg, speed_kmh
        if state_changed or self._due("cart.telemetry", TELEMETRY_INTERVAL_S):
            self.broadcaster.publish("cart.telemetry", CartTelemetry(
                cart_id=cart.id, state=cart.state, position=cart.position,
                heading_deg=cart.heading_deg, speed_kmh=cart.speed_kmh,
            ))
        self._update_escort()

    def scan_progress(self, phase: str, progress: float, altitude_m: float, heading_deg: float,
                      coverage_pct: float) -> None:
        mission = self.store.active_mission
        if mission is None or mission.state not in (S.TAKING_OFF, S.SCANNING):
            return
        mission.scan = ScanInfo(phase=phase, progress=round(progress, 3), coverage_pct=round(coverage_pct, 1))
        phase_key = (mission.id, phase)
        if phase_key != self._scan_phase_sent or self._due("scan.progress", TELEMETRY_INTERVAL_S):
            self._scan_phase_sent = phase_key  # every phase is broadcast at least once
            self.broadcaster.publish("scan.progress", ScanProgress(
                mission_id=mission.id, phase=phase, progress=mission.scan.progress,
                altitude_m=altitude_m, heading_deg=heading_deg, coverage_pct=mission.scan.coverage_pct,
            ))

    def route_scores(self, routes: list, final: bool) -> None:
        """Relay /route_scores during the scan (fills mission.routes for pre-selection)."""
        mission = self.store.active_mission
        if mission is None or mission.state is not S.SCANNING:
            return
        mission.routes = with_scores(list(routes))
        if mission.preselected_route not in {r.id for r in routes}:
            mission.preselected_route = None
        self.broadcaster.publish("route.scores", RouteScores(mission_id=mission.id, routes=mission.routes, final=final))

    def payload_confirmed(self, speaker: bool, led: bool) -> None:
        mission = self.store.active_mission
        if mission is None:
            return
        mission.payload = PayloadState(speaker=speaker, led=led)
        self._emit_payload(mission)

    def ros_status(self, ok: bool, label: str) -> None:
        """Header chip: ROS link (API_CONTRACT §3.2). Broadcast when it changes."""
        ros = self.store.system.ros
        if (ros.ok, ros.label) != (ok, label):
            ros.ok, ros.label = ok, label
            self.broadcaster.publish("system.status", self.system_status())

    def detector_fps(self, fps: float) -> None:
        """Header chip: measured /person_detections rate (API_CONTRACT §3.2)."""
        jetson = self.store.system.jetson
        fps = round(fps, 1)
        changed = (jetson.ok, round(jetson.fps)) != (fps > 0, round(fps))
        jetson.ok, jetson.fps = fps > 0, fps
        self.store.drone.detector.fps = fps
        if changed:
            self.broadcaster.publish("system.status", self.system_status())

    def camera_status(self, available: bool, resolution: "ImageSize | None" = None) -> None:
        camera = self.store.camera
        camera.available = available
        if resolution is not None:
            camera.resolution = resolution
        self.broadcaster.publish("camera.info", camera)

    def camera_detections(self, boxes: list, image_size: ImageSize, sim_time_s: "float | None" = None) -> None:
        if not self._due("camera.detections", 1.0 / self.settings.detections_max_hz):
            return
        mission = self.store.active_mission
        counts = None
        if mission is not None and mission.state is S.SCANNING and mission.routes:
            counts = {r.id: r.people_count for r in mission.routes}
        self.broadcaster.publish("camera.detections", CameraDetections(
            drone_id=self.store.drone.id,
            mission_id=mission.id if mission else None,
            mode=self.store.camera.mode,
            heading_deg=self.store.drone.heading_deg,
            frame_stamp=self.now(),
            sim_time_s=sim_time_s,
            image_size=image_size,
            boxes=boxes,
            counts=counts,
        ))

    # ======================================================================
    # Dev
    # ======================================================================

    def reset(self) -> None:
        """Back to boot state (POST /api/dev/sim/reset). Sources are reset by the caller."""
        self._cancel_timer()
        self.store.reset_from(create_default_store(self.settings, self.world))
        self._last_sent.clear()
        self._scan_phase_sent = ()

    # ======================================================================
    # Internals
    # ======================================================================

    def _due(self, key: str, interval_s: float) -> bool:
        now = time.monotonic()
        if now - self._last_sent.get(key, -1e9) >= interval_s:
            self._last_sent[key] = now
            return True
        return False

    def _active(self) -> Mission:
        mission = self.store.active_mission
        if mission is None:
            raise ValueError("no active mission")
        return mission

    def _command(self, mission_id: str, command: Command) -> Mission:
        mission = self.get_mission(mission_id)  # 404
        ensure_command_allowed(mission.state, command)  # 409 (closed missions are idle)
        return mission

    def _transition(self, mission: Mission, trigger: Trigger) -> S:
        from_state = mission.state
        mission.state = transition(from_state, trigger)
        mission.state_entered_at = self.now()
        if mission.state is not S.IDLE:
            mission.timeline.append(TimelineEntry(state=mission.state, at=mission.state_entered_at))
        return from_state

    def _route(self, mission: Mission, route_id: "str | None") -> Route:
        return next(r for r in mission.routes if r.id == route_id)

    def _select_route_command(self, mission: Mission, route: str, source: str) -> MissionCommand:
        return MissionCommand(mission.id, "select_route", route=route, source=source,
                              polyline_local=self._leg_local(mission, route, "outbound"))

    def _lock_route_in_background(self, mission: Mission, route: str, source: str) -> None:
        """Countdown expiry or a pre-selected route: no HTTP caller waits for the acks."""
        self._cancel_timer()
        mission_id = mission.id

        def commit():
            m = self.store.active_mission
            if m is not None and m.id == mission_id and m.state is S.ROUTE_SELECTED:
                self._commit_lock(m, route, source)
        self._send_in_background(self._select_route_command(mission, route, source), commit,
                                 mission_id)

    def _commit_lock(self, mission: Mission, route: str, source: str) -> None:
        """route_selected -> escorting, once the nodes have accepted select_route."""
        self._cancel_timer()
        from_state = self._transition(mission, Trigger.ROUTE_LOCKED)
        mission.selected_route = route
        mission.selection_source = source
        mission.escort = EscortInfo(leg="outbound", eta_s=self._route(mission, route).est_time_s)
        return_path = self._leg_local(mission, route, "return") or []
        mission.return_polyline = [self.local_position(p) for p in return_path] or None
        mission.payload = PayloadState(speaker=True, led=True)
        self.bus.publish(PayloadCommand(mission.id, speaker=True, led=True))
        self.broadcaster.publish("route.selected", RouteSelected(mission_id=mission.id, route=route, source=source))
        self._emit_state(mission, from_state)
        self._emit_payload(mission)

    def _auto_select_fire(self, mission_id: str) -> None:
        mission = self.store.active_mission
        if mission and mission.id == mission_id and mission.state is S.ROUTE_SELECTED:
            self.store.runtime.auto_select_handle = None
            self._lock_route_in_background(mission, mission.recommended_route, "auto")

    def _cancel_timer(self) -> None:
        runtime = self.store.runtime
        if runtime and runtime.auto_select_handle is not None:
            runtime.auto_select_handle.cancel()
            runtime.auto_select_handle = None

    def _commit_abort(self, mission: Mission, reason: str) -> None:
        self._cancel_timer()
        from_state = self._transition(mission, Trigger.ABORT)
        mission.abort_reason = reason
        self._emit_state(mission, from_state, reason=reason)

    # --- sending commands (ROS_TOPICS §4.11: command_ack from every node that acts) --------

    async def _send(self, cmd) -> None:
        """Publish, then wait for the acks (REST callers). Mock parts: returns at once."""
        waiter = self.bus.register_acks(cmd)
        self.bus.publish(cmd)
        if waiter is None:
            return
        try:
            await waiter
        except CommandRejected as exc:
            raise ApiError(409, "command_rejected", exc.message, {
                "command": exc.command, "node": exc.node, "reason": exc.reason}) from exc
        except CommandTimeout as exc:
            raise ApiError(504, "ros_timeout", exc.message, {
                "command": exc.command, "nodes": exc.nodes, "timeoutS": exc.timeout_s}) from exc

    def _send_in_background(self, cmd, commit, mission_id: str) -> None:
        """Publish from a timer or a ROS hook; commit once acknowledged. On a rejection or a
        timeout the mission goes to error (command_rejected / command_timeout)."""
        waiter = self.bus.register_acks(cmd)
        self.bus.publish(cmd)
        if waiter is None:
            commit()
            return

        async def finish():
            try:
                await waiter
            except CommandFailed as exc:
                self._command_failed(mission_id, exc)
                return
            commit()
        task = asyncio.get_running_loop().create_task(finish())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _command_failed(self, mission_id: str, exc: CommandFailed) -> None:
        log.warning("command failed: %s", exc.message)
        mission = self.store.active_mission
        if mission is None or mission.id != mission_id or mission.state in (S.IDLE, S.ERROR):
            return
        code = "command_timeout" if isinstance(exc, CommandTimeout) else "command_rejected"
        self.fault(code, exc.message)

    def local_position(self, point) -> Position:
        """World ENU metres -> Position (SVG x/y = metres if the projection is switched off)."""
        if self.projection.configured:
            return self.projection.local_to_position(*point)
        return Position(lat=0.0, lon=0.0, x=point[0], y=point[1])

    def _leg_local(self, mission: Mission, route_id: str, leg: str) -> "list | None":
        """[[e, n], ...] of a leg: the world's cart path / return path for a world route,
        else the route's own polyline (reversed for the return)."""
        if route_id in self.world.routes:
            path = self.world.cart_path(route_id) if leg == "outbound" else self.world.return_path(route_id)
        else:
            route = next((r for r in mission.routes if r.id == route_id), None)
            if route is None or not self.projection.configured:
                return None
            path = [self.projection.position_to_local(p) for p in route.polyline]
            if leg == "return":
                path.reverse()
        return [[round(e, 3), round(n, 3)] for e, n in path]

    def _update_escort(self) -> None:
        """escort.progress from the cart pose projected onto the active leg's path (metres)."""
        mission = self.store.active_mission
        if mission is None or mission.state not in (S.ESCORTING, S.RETURNING) or mission.selected_route is None:
            return
        if mission.state is S.RETURNING and self.store.cart.state == "at_hub":
            return  # leg finished (cart_at_hub set progress 1, eta 0); waiting for the landing
        route = self._route(mission, mission.selected_route)
        points = self._leg_local(mission, mission.selected_route, mission.escort.leg or "outbound")
        cart_local = self.projection.position_to_local(self.store.cart.position)
        if not points or len(points) < 2 or cart_local is None:
            return
        length = path_length(points)
        if length <= 0:
            return
        cart_s = project(points, cart_local)[0]
        escort = mission.escort
        escort.progress = round(cart_s / length, 4)
        escort.elapsed_s = round((self.now() - mission.state_entered_at).total_seconds(), 1)
        if route.est_time_s is not None:
            escort.eta_s = round(max(0.0, route.est_time_s - escort.elapsed_s), 1)
        drone = self.store.drone
        if not drone.docked_on_cart:
            drone_s = project(points, self.projection.position_to_local(drone.position))[0]
            escort.drone_ahead_of_cart_m = round(drone_s - cart_s, 1)
        if self._due("escort.progress", ESCORT_INTERVAL_S):
            self.broadcaster.publish("escort.progress", EscortProgress(
                mission_id=mission.id, leg=escort.leg, progress=escort.progress, elapsed_s=escort.elapsed_s,
                eta_s=escort.eta_s, drone_ahead_of_cart_m=escort.drone_ahead_of_cart_m,
            ))

    def _result(self, mission: Mission) -> MissionResult:
        started = next(e.at for e in mission.timeline if e.state is S.INCIDENT_RECEIVED)
        selected = self._route(mission, mission.selected_route)
        others = [r.est_time_s for r in mission.routes if r.id != selected.id]
        vs_best = None
        if selected.est_time_s is not None and others and None not in others:
            vs_best = round(selected.est_time_s - min(others), 1)
        return MissionResult(
            response_time_s=round((self.now() - started).total_seconds(), 1),
            vs_best_alternative_s=vs_best,
            people_warned=selected.people_count,
            drone_battery_pct=self.store.drone.battery_pct,
        )

    def _close_out(self, mission: Mission, outcome: str, from_state: S) -> None:
        """Mission is back at idle: record history, clear /incident, activate next queued."""
        self._cancel_timer()
        incident = self.store.active_incident
        mission.outcome = outcome
        mission.ended_at = self.now()
        incident.status = outcome  # same names: resolved | transferred | cancelled | aborted | error
        launched = any(e.state is S.TAKING_OFF for e in mission.timeline)
        summary = MissionSummary(
            id=mission.id,
            reported_at=incident.reported_at,
            incident_type=incident.type,
            zone=incident.location.zone,
            route=mission.selected_route,
            route_label=self._route(mission, mission.selected_route).label if mission.selected_route else None,
            response_time_s=mission.result.response_time_s if mission.result else None,
            drone_id=mission.drone_id if launched else None,
            outcome=outcome,
        )
        self.store.add_closed(mission, summary, incident)
        self.store.active_mission = None
        self.store.active_incident = None
        self.store.runtime = None

        self.bus.publish(IncidentCommand(active=False))
        self._emit_state(mission, from_state)
        self._emit_incident("incident.updated", incident)
        self.broadcaster.publish("mission.history", MissionHistoryEvent(item=summary))

        nxt = self.store.pop_next_queued()
        if nxt is not None:
            self._activate(nxt, created=False)

    # --- WS ---------------------------------------------------------------------

    def _emit_state(self, mission: Mission, from_state: S, reason: "str | None" = None) -> None:
        self.broadcaster.publish("mission.state", MissionStateEvent(
            mission_id=mission.id, from_=from_state, to=mission.state, reason=reason, mission=mission,
        ))

    def _emit_incident(self, type_: str, incident: Incident) -> None:
        self.broadcaster.publish(type_, IncidentEvent(incident=incident, open_incidents=self.store.open_incidents()))

    def _emit_payload(self, mission: Mission) -> None:
        self.broadcaster.publish("payload.state", PayloadStateEvent(
            mission_id=mission.id, speaker=mission.payload.speaker, led=mission.payload.led,
        ))


def with_scores(routes: list) -> list:
    """Derive Route.score (contract §3.7): the fastest estTimeS / this route's estTimeS, so the
    fastest route scores 1.0. Node 2 only produces estimatedTimeS. null while estTimeS is null."""
    times = [r.est_time_s for r in routes if r.est_time_s]
    fastest = min(times) if times else None
    return [
        r.model_copy(update={"score": round(fastest / r.est_time_s, 3) if fastest and r.est_time_s else None})
        for r in routes
    ]
