"""SOURCE_ROUTE=ros: Node 2 (sabeq_ros autonomy_node) + the backend's live counts mirror.

- /scan/active_route + /person_detections -> live per-route counts for display, with Node 2's
  exact rule (autonomy_node.py): the first /scan/active_route of a scan resets every count;
  while a route is active, its count is the highest len(detections) seen. Like Node 2 it counts
  by ARRIVAL, so frames arriving while the drone turns to the next route still count for the
  previous one (ROS_TOPICS doc/code differences).
- /route_scores (Node 2's JSON, once at scan end) -> final routes; the service derives score.
- /selected_route (plain route name) -> route_recommended -> the 3 s countdown.
Node 2's messages carry no missionId: they apply to the active mission.
"""
import logging
import statistics
import time

from app.ros import topics as T
from app.ros.converters import live_routes, scored_routes
from app.ros.wire import BEST_EFFORT_5, RELIABLE_10
from app.sources.base import RouteSource
from app.sources.commands import IncidentCommand, MissionCommand
from app.state.machine import InvalidTransition, MissionState as S

log = logging.getLogger(__name__)

LIVE_INTERVAL_S = 0.5   # live route.scores at most twice a second (real time)


class RosRouteSource(RouteSource):

    def __init__(self, service, store, settings, ctx):
        super().__init__(service, store, settings)
        self.ctx = ctx
        ctx.register_part(self.part)
        self.reset()

    def reset(self) -> None:
        self.counts: dict = {}
        self.samples: dict = {}
        self.active = None
        self.in_progress = False
        self.final = None
        self._last_live = 0.0

    @property
    def world(self):
        return self.service.world

    async def start(self) -> None:
        await self.ctx.start()
        self.ctx.subscribe(T.SCAN_ACTIVE_ROUTE, "string", RELIABLE_10, self._on_active_route)
        # IMPORTANT: autonomy_node selects routes from CENTER ROI detections, not
        # the full camera frame. Use the same topic so dashboard counts match Node 2.
        self.ctx.subscribe(T.PERSON_DETECTIONS_CENTER, "detections", BEST_EFFORT_5, self._on_detections)
        self.ctx.subscribe(T.ROUTE_SCORES, "json", RELIABLE_10, self._on_route_scores)
        self.ctx.subscribe(T.SELECTED_ROUTE, "string", RELIABLE_10, self._on_selected_route)

    async def stop(self) -> None:
        await self.ctx.stop()

    def handle(self, cmd) -> None:
        if isinstance(cmd, IncidentCommand) or (isinstance(cmd, MissionCommand)
                                                and cmd.command == "launch"):
            self.reset()       # a new mission: a fresh scan
        self.ctx.publish_command(cmd)

    # --- Node 2's counting rule -----------------------------------------------------------------
    def _on_active_route(self, name: str) -> None:
        # mission_node intentionally publishes an empty route while rotating.
        # It is a valid scan transition, not an error.
        if not name:
            self.active = None
            return
        if name not in self.world.routes:
            log.warning("/scan/active_route %r is not a route of routes.yaml", name)
            return
        if not self.in_progress:
            self.counts = {rid: 0 for rid in self.world.routes}
            self.samples = {rid: [] for rid in self.world.routes}
            self.in_progress, self.final = True, None
        self.active = name
        self.samples.setdefault(name, [])
        self._push_live(force=True)

    def _on_detections(self, msg) -> None:
        if not self.in_progress or self.active is None:
            return
        n = len(getattr(msg, "detections", None) or [])
        samples = self.samples.setdefault(self.active, [])
        samples.append(n)
        # Mirror autonomy_node exactly: stable count = rounded median of CENTER ROI frames.
        stable = int(round(statistics.median(samples)))
        if stable != self.counts.get(self.active, 0):
            self.counts[self.active] = stable
            self._push_live()

    def _push_live(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_live < LIVE_INTERVAL_S:
            return
        self._last_live = now
        self.service.route_scores(live_routes(self.world, self.counts, self.service.local_position),
                                  final=False)

    def _on_route_scores(self, scores: dict) -> None:
        routes = scored_routes(self.world, scores, self.service.local_position)
        if not routes:
            log.warning("/route_scores without any known route: %s", list(scores))
            return
        self.final = routes
        self.in_progress, self.active = False, None   # Node 2 ends the scan here
        self.service.route_scores(routes, final=True)

    def _on_selected_route(self, name: str) -> None:
        mission = self.store.active_mission
        if mission is None or mission.state is not S.SCANNING:
            log.info("ignoring /selected_route %s (mission state %s)", name,
                     mission.state.value if mission else "idle")
            return
        routes = self.final or live_routes(self.world, self.counts, self.service.local_position)

        # Keep the dashboard recommendation, ETA and executed route consistent.
        # Node 2 historically breaks exact ties by insertion order (A before C), which
        # made A_west appear even when A/C had identical crowd + distance. Here we use
        # the SAME estimated-time metric displayed by the dashboard, and only use a
        # deterministic demo tie-breaker when estimates are exactly equal.
        final_with_eta = [r for r in routes if r.est_time_s is not None]
        if final_with_eta:
            tie_priority = {"C_east": 0, "A_west": 1, "B_north": 2}
            chosen = min(
                final_with_eta,
                key=lambda r: (float(r.est_time_s), tie_priority.get(r.id, 99)),
            ).id
            if chosen != name:
                log.info(
                    "normalizing Node 2 recommendation %s -> %s using dashboard ETA",
                    name, chosen,
                )
            name = chosen

        try:
            self.service.route_recommended(routes, name)
        except (ValueError, InvalidTransition) as exc:
            log.warning("cannot use /selected_route %s: %s", name, exc)
