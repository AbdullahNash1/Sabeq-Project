# Sabeq Backend

FastAPI backend for the Sabeq dashboard (`../Frontend`). It sits between the ROS 2 nodes and the Vue app. It owns the mission state machine, relays ROS data over REST and WebSocket, and forwards operator commands to ROS.

## Sources of truth — read before changing anything

1. **`API_CONTRACT.md` is the source of truth for the API.** Don't add fields, endpoints, WS message types, error codes or enum values that aren't in it. If something is missing, propose a contract change first, then implement it.
2. **`ros_samples.md`** holds real outputs from the ROS machine. It is the ground truth for nodes that are already implemented (`yolo_node`, the Gazebo camera). Where it disagrees with the other docs, it wins.
3. **`ROS_TOPICS.md`** (v2.1) is the ROS spec: the implemented Node 1 (`yolo_node`) and Node 2 (`autonomy_node`) in `ros_ws/src/sabeq_ros`, our `mission_node` and `world_node` (`ros_ws/src/sabeq_mission`), and `backend_bridge` (this backend). §9 lists the doc/code differences.
4. **The world** (`../docs/WORLD.md`): route names, waypoints, incident, start, First Aid Center and scan yaws come **only** from `WORLD_META` (`../docs/team/world_meta.json`) and `ROUTES_YAML` (`../ros_ws/src/sabeq_ros/config/routes.yaml`), loaded by `app/world.py`. **Never edit `docs/team/` or `ros_ws/src/sabeq_ros/`** (teammates' files); `routes.yaml` is ours and is checked by `tools/check_routes.py`.
5. **`FRONTEND_TODO.md`** is the frontend work the contract implies. This repo doesn't change frontend code.

## Hard rules from the contract

- **camelCase JSON on the wire.** Python models are snake_case. Always subclass `app.models.base.ApiModel`, whose alias generator converts names. Request bodies subclass `RequestModel` (`extra="forbid"`).
- **Route ids are a dynamic list:** the route names in `routes.yaml` (`A_west`, `B_north`, `C_east`), read through `app/world.py`. Never hard-code route ids or the number of routes. Per-route values are `dict[RouteId, ...]`. People see `Route.label` (localised `{en, ar}` from `ROUTE_LABELS`), never the id.
- **ROS stamps are sim time.** Never convert them to ISO dates. ISO fields (`ts`, `frameStamp`) are the backend's receive or send time, and raw stamps go in `simTimeS`.
- **The backend doesn't compute routes.** `estTimeS` and the recommendation come from Node 2. The backend only derives `score` from `estTimeS` (`service.with_scores`: fastest / own, fastest = 1.0), runs the 3 s auto-select countdown and the operator override.
- **The world has one incident** at Gazebo (0, 112). `POST /incidents` always uses it; posted coordinates are validated and ignored.
- **Missions end on the drone's landing.** `arrived` tracks `mission.rescue` (`in_progress` → `patient_loaded`); `start_return` is refused (409) until the patient is loaded; the cart reaching the First Aid Center (the hub) is not a transition; `returning → completed` happens on `drone_landed()`.
- **Errors use the envelope** `{"error":{"code","message","details"}}`. Validation errors are **400** (not 422). Raise `ApiError(status, code, message)` for contract error codes. Invalid state or command combinations raise `InvalidTransition` (409).
- **v1 scope:** one drone `SBQ-01`, one cart `CART-01`, in-memory only (last 10 closed missions), no auth, no pagination, MJPEG only. Everything else is in contract §8, *Deferred to v2*.

## Structure

```
app/
  main.py          create_app(settings, sources_factory=None, record_commands=False):
                   wires store, service, broadcaster, command bus and sources; lifespan
                   starts/stops sources and broadcasts system.status every 2 s
  config.py        Settings (pydantic-settings). SABEQ_* plus unprefixed SOURCE_*, MOCK_*, DEV_MODE,
                   WORLD_META, ROUTES_YAML, ROUTE_LABELS. Relative paths resolve from Backend/.
  world.py         load_world(): the two world files, cross-checked at startup (same rules as
                   tools/check_routes.py; WorldConfigError on any mismatch or a missing label).
                   World: routes (path, length, scan yaw, scan-zone people, label), cart_path(),
                   return_path() (demo_common.build_return_path), start, incident, First Aid Center.
                   ENU helpers: along, project, path_length, compass_deg
  errors.py        ApiError + exception handlers → error envelope (400 for validation)
  service.py       MissionService — the ONLY place state changes:
                   REST commands (launch, dismiss, select_route, set_payload, abort,
                   start_return, close, create_incident) and hooks for data sources:
                     milestones  scan_altitude_reached, route_recommended, cart_at_incident,
                                 patient_loaded, cart_at_hub (no transition), drone_landed,
                                 fault, battery_low
                     telemetry   drone_telemetry, scan_progress, payload_confirmed,
                                 cart_telemetry (+ computes escort.progress), route_scores,
                                 camera_status, camera_detections
                   Owns the 3 s auto-select timer and WS rate limits.
  broadcaster.py   Broadcaster.publish(type, model): WS fan-out, callable from any thread
  geo.py           §2 projection (world ENU metres <-> lat/lon <-> SVG); defaults fit the site
  models/          Pydantic models for every model in API_CONTRACT.md
  state/
    machine.py     PURE mission state machine (no FastAPI / Pydantic / ROS imports)
    store.py       In-memory Store + MissionRuntime (backend-internal, never serialised)
  ros/
    convert.py     PURE Detection2DArray → API boxes
    messages.py    typed command → ROS JSON (ROS_TOPICS §4.11/4.12/4.15)
    topics.py      topic names;  wire.py  QoS profiles + ROS msg <-> Python (string, bool,
                   json, image, detections), no rclpy import
    converters.py  PURE node JSON → hook arguments (drone/cart status + pose, Node 2 scores)
    transport.py   Transport interface; RclpyTransport (lazy rclpy import, node backend_bridge,
                   MultiThreadedExecutor thread, callbacks handed over with call_soon_threadsafe)
    context.py     RosContext: one per app, shared by the ROS parts. /mission_events dispatch by
                   source, publishes each command once, waits for command_ack (the bus ack gate)
    fake.py        FakeRosGraph / FakeTransport: an in-process ROS graph with DDS QoS matching
  sources/
    base.py        Source ABC + CameraSource/RouteSource/DroneSource/CartSource, SourceSet
    commands.py    MissionCommand, PayloadCommand, IncidentCommand
    bus.py         CommandBus: service publishes once → every part's source (+ recorder)
    factory.py     build_sources(): SOURCE_<PART>=mock|ros
    mock/          sim.py (Ticker, MOCK_SPEED, team timings), route.py (Node 2 + live counts),
                   drone.py (mission_node), cart.py (world_node: cart + rescue),
                   camera.py (Pillow MJPEG frames + boxes)
    ros/           camera.py (MJPEG from /camera/image, detections, detection fps = system.jetson, the YOLO chip), route.py (Node 2
                   mirror + /route_scores + /selected_route), drone.py (mission_node), cart.py
                   (world_node + Reset sim)
    null.py        NullSources (API tests drive the hooks themselves)
    recording.py   RecordingSink (commands in ROS wire format, for tests)
  routers/         /api REST: system.py, fleet.py (incl. MJPEG), incidents.py, missions.py,
                   dev.py (POST /api/dev/sim/reset, only when DEV_MODE=true)
  ws/endpoint.py   /ws: snapshot on connect, ping/pong, close after 30 s without ping
tests/             pytest + TestClient
```

Rules for new code:
- Handlers are `async def`, so every store mutation happens on the event loop. Code running in another thread (for example ROS callbacks) must use `loop.call_soon_threadsafe` to call the service. `Broadcaster.publish` already does this hand-over itself.
- Routers stay thin: validate the body, call `MissionService`, and return the result. The service does the state machine check, store mutation, command publish and WS broadcast, in that order.

## Data sources

There are four parts: **camera, route, drone and cart**. Each is `mock` or `ros` independently, via `SOURCE_CAMERA`, `SOURCE_ROUTE`, `SOURCE_DRONE` and `SOURCE_CART`. The default is mock.

- **Sources change state only by calling `MissionService` hooks.** They may *read* the store, but they never write to it. The service never knows which sources are mock and which are ROS.
- **Commands go out on the `CommandBus`.** The service publishes each typed command once, and every part's active source receives it (along with `RecordingSink` in tests).
- **`handle(cmd)` only records intent.** Act on the next tick, and never call a hook from inside `handle`, because the service is in the middle of a transition when it publishes.
- **Mocks tick on the event loop** (`Ticker`). Simulated time per tick is `MOCK_TICK_S × MOCK_SPEED`. Pace in-sim activity in sim time, but leave WS rate limits in real time (the service enforces those).
- **Mocks read each other only through the store,** so mixed modes work. For example, the mock drone escorts ahead of `store.cart.position`, whichever source moves the cart.
- **Mocks simulate in world ENU metres** (`app/world.py`) and project to `Position` only when they report (`service.local_position`). They mirror `docs/team/mission.py` and `sim_node.py`: take-off to 8 m, three scan headings (yaws from `world_meta.json`), cart at 10 m/s with the drone 15 m ahead, the rescue timing, the return to the First Aid Center and the landing on the cart. The constants and their team sources are in `sources/mock/sim.py`.
- **Node 2's format lives only in the route source:** the count while each route is faced, `distance / 7.0 + people × 0.5`, and `MOCK_BEST_ROUTE` (a route id). The default winner is C_east (scan-zone people 18 / 26 / 10 from `world_meta.json`).
- **ROS sources** (`sources/ros/`) share one `RosContext` (one rclpy node, `backend_bridge`). Message formats follow what the nodes' **code** publishes (`ROS_TOPICS.md` §9 lists where it differs from the docs). They project `localM` into a `Position` (`service.local_position`) before calling the hooks. rclpy is imported only when a part is `ros`; without ROS the app fails at startup with `RosUnavailable` and the setup hint.
- **Commands are ack-gated with ROS.** `MissionService` commands are `async`: `await self._send(cmd)` publishes on the bus, then waits for `command_ack` from each acting node (`RosContext.expected_acks`; 3 s, `command_ack_timeout_s`) and only then commits the transition. Rejected → `409 command_rejected`, no ack → `504 ros_timeout`. Commands the service sends by itself (countdown, pre-select, low battery) go through `_send_in_background`: a failure moves the mission to `error`. With mock sources there is no ack gate and everything commits at once.

Demo tips: `MOCK_SPEED=5` speeds the demo up, `MOCK_BEST_ROUTE=B_north` gives a rehearsed winner, `MOCK_LOW_BATTERY=true` shows the abort, and `DEV_MODE=true` enables `POST /api/dev/sim/reset` to start over.

## Commands

Requires **Python 3.10**, which is ROS 2 Humble compatible. Don't use 3.11+ features (`StrEnum`, `datetime.UTC`, `typing.Self`).

```sh
# setup
python3.10 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env            # optional

# run (dev)
.venv/bin/uvicorn app.main:app --reload --port 8000

# test
.venv/bin/python -m pytest -q
```

On the ROS machine, `rclpy` and `vision_msgs` come from apt, not pip. Run `source /opt/ros/humble/setup.bash` before starting with any `SOURCE_*=ros`. When you create the venv there, use `--system-site-packages` so it can see them. Step by step: `README.md`.

## Testing conventions

- `tests/test_machine.py` keeps a **hand-written copy** of the contract's transition and command tables. If you change the contract's state machine, update both `machine.py` and that table.
- `tests/test_convert.py` uses the real sample from `ros_samples.md` §1. The expected result is x 77.94, y 60.52, w 0.98, h 4.78, conf 0.806.
- API tests use `manual_app()` / the `app` fixture, which combine null sources with a `RecordingSink`. `drv.drive_to("<state>")` (tests/conftest.py) gets a mission into any state, by calling the service hooks on the app loop with `drv.on_loop(...)`. The `ros` fixture holds the recorded commands in ROS format.
- Mock end-to-end tests (`tests/test_mock_mission.py`) use the real mock sources with `mock_speed=150, mock_tick_s=0.01` and drive them only through REST. Anything that has to be observed in WS messages during a short phase (the scan headings, both escort legs) needs a slower speed (8–20), because WS messages are rate-limited in real time.
- `tests/test_world.py` checks the loader against `docs/team/demo_common.py` and `tools/check_routes.py` (imported read-only, without writing `__pycache__` there). Broken `routes.yaml` variants are written to `tmp_path`, never over the real file.
- `drv.drive_to("completed")` goes through `patient_loaded` and `drone_landed`; `make_routes()` uses ids outside the world (A/B/C) on purpose, so the service's polyline fallback stays covered.
- Use `Settings(_env_file=None, ...)` in tests, so a developer's `.env` can't leak in.
- **ROS tests need no ROS.** `tests/test_ros_sources.py` runs the ROS sources on `FakeRosGraph` (via `build_sources(..., ros_transport=FakeTransport(graph))`) with the exact messages the nodes build. `tests/test_ros_cosim.py` is the end-to-end co-simulation: the real `sabeq_mission` `MissionLogic` / `WorldLogic` plus a line-by-line Node 2 and a fake YOLO (`tests/rosim.py`), stepped on one sim clock inside the app loop, driven only via REST + WS. Hooks with keyword arguments go through `client.portal.call(functools.partial(...))`.
