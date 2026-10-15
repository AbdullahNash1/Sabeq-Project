"""Mock Node 2 (autonomy_node) plus the backend's live count mirror (ROS_TOPICS §4.6, §6.1).

Everything route-specific comes from the world files (app/world.py): ids, paths,
labels and the scan-zone crowd. During the scan, only the route being scanned counts,
as Node 2 counts only for the route on /scan/active_route: the route whose turn it is
in scan order (from the drone's scan progress), and only once the drone faces it
within the scan yaw tolerance. A route the drone merely passes while turning to the
next one never counts. Its count rises to its scan-zone people, as Node 2 keeps the
highest YOLO count while that route is active. When the scan is done:
estimatedTimeS = distance / 7.0 + people x 0.5 (Node 2's formula), final scores,
then route_recommended(fastest). The service derives score from estTimeS.

MOCK_BEST_ROUTE raises the other routes' counts until that route is clearly fastest.
"""
import math

from app.models import Route
from app.sources.base import RouteSource
from app.sources.commands import IncidentCommand, MissionCommand
from app.sources.mock.sim import SCAN_HOLD_S, SCAN_YAW_TOL_DEG, Ticker
from app.state.machine import MissionState as S

NODE2_SPEED_MPS = 7.0  # autonomy_node vehicle_speed (a scoring constant, not the cart speed)
NODE2_DELAY_PER_PERSON_S = 0.5  # autonomy_node delay_per_person
SCORES_INTERVAL_S = 0.5  # sim seconds between live route.scores while counts change
BEST_MARGIN = 1.15  # MOCK_BEST_ROUTE: every other route at least 15 % slower


def node2_est_time_s(distance_m: float, people: int) -> float:
    return round(distance_m / NODE2_SPEED_MPS + people * NODE2_DELAY_PER_PERSON_S, 2)


class MockRouteSource(RouteSource):
    def __init__(self, service, store, settings):
        super().__init__(service, store, settings)
        self._ticker = Ticker(settings, "route")
        self.reset()

    @property
    def world(self):
        return self.service.world

    def reset(self) -> None:
        self._mission_id = None
        self._targets: dict = {}  # id -> people Node 2 will see on that route
        self._counts: dict = {}  # id -> live count (float, shown as int)
        self._final_sent = False
        self._since_scores = 0.0
        self._last_sent = None

    async def start(self) -> None:
        self._ticker.start(self._step)

    async def stop(self) -> None:
        await self._ticker.stop()

    # --- commands -------------------------------------------------------------

    def handle(self, cmd) -> None:
        if isinstance(cmd, IncidentCommand):
            self.reset()
            if cmd.active:
                self._mission_id = cmd.mission_id
                self._targets = self._scan_targets()
                self._counts = {rid: 0.0 for rid in self._targets}
        elif isinstance(cmd, MissionCommand) and cmd.command == "abort":
            self._final_sent = True  # stop scoring

    def _scan_targets(self) -> dict:
        targets = {rid: r.scan_people for rid, r in self.world.routes.items()}
        best = self.settings.mock_best_route
        if best is None:
            return targets
        best_t = node2_est_time_s(self.world.routes[best].length_m, targets[best])
        for rid, r in self.world.routes.items():
            if rid != best:
                needed = math.ceil((BEST_MARGIN * best_t - r.length_m / NODE2_SPEED_MPS) / NODE2_DELAY_PER_PERSON_S)
                targets[rid] = max(targets[rid], needed)
        return targets

    # --- model ------------------------------------------------------------------

    def _routes(self, final: bool) -> list:
        routes = []
        for rid, r in self.world.routes.items():
            people = self._targets[rid] if final else int(self._counts[rid])
            routes.append(Route(
                id=rid,
                label=r.label,
                length_m=round(r.length_m, 2),
                people_count=people,
                count_final=final,
                crowd_factor=None,  # Node 2 has no crowd factor
                est_time_s=node2_est_time_s(r.length_m, people) if final else None,
                polyline=[self.service.local_position(p) for p in r.path],
            ))
        return routes

    def _active_route(self, mission) -> "str | None":
        """The route on /scan/active_route: the one whose turn it is (scan progress counts
        routes done, in scan order) once the drone faces it. None while turning."""
        if mission.scan.phase != "rotate" or self.store.drone.state != "scanning":
            return None
        ids = self.world.route_ids
        # coveragePct is rounded to 0.1 (1/3 arrives as 33.3): allow for that
        turn = int(mission.scan.coverage_pct * len(ids) / 100.0 + 0.01)
        if turn >= len(ids):
            return None
        facing = self.world.route_facing(self.store.drone.heading_deg, SCAN_YAW_TOL_DEG)
        return ids[turn] if facing == ids[turn] else None

    # --- tick ---------------------------------------------------------------------

    def _step(self, dt: float) -> None:
        mission = self.store.active_mission
        if (mission is None or mission.id != self._mission_id or self._final_sent
                or mission.state is not S.SCANNING or not self._targets):
            return
        if mission.scan.phase == "done":
            self._final_sent = True
            routes = self._routes(final=True)
            best = min(routes, key=lambda r: r.est_time_s).id
            self.service.route_scores(routes, final=True)
            self.service.route_recommended(routes, best)
            return
        if self._last_sent is None:  # scan started: the route list (all zero) for display and pre-selection
            self._last_sent = {rid: 0 for rid in self._counts}
            self.service.route_scores(self._routes(final=False), final=False)
        active = self._active_route(mission)
        if active is not None:
            target = self._targets[active]
            self._counts[active] = min(target, self._counts[active] + target * dt / SCAN_HOLD_S)
        self._since_scores += dt
        shown = {rid: int(n) for rid, n in self._counts.items()}
        if self._since_scores >= SCORES_INTERVAL_S and shown != self._last_sent:
            self._since_scores = 0.0
            self._last_sent = shown
            self.service.route_scores(self._routes(final=False), final=False)
