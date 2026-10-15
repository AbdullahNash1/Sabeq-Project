# Sabeq Backend — API Contract

Status: **v1.5**. This is the scope for the first backend build. Anything outside it is listed in §8, *Deferred to v2*.

**Changes in v1.5** (the backend's ROS sources; ROS side in [`ROS_TOPICS.md`](./ROS_TOPICS.md) v2.1, §4.11):

1. **Commands are acknowledged before the mission moves on.** With ROS sources, `launch`, `route`, `return` and `abort` reply only after every acting ROS node has acknowledged the command (up to 3 s). With mock sources nothing changes (§4.4, §6.1).
2. **New errors** `409 command_rejected` (a node refused the command; `details: {command, node, reason}`) and `504 ros_timeout` (no ack within 3 s; `details: {command, nodes, timeoutS}`). The mission keeps its state (§1.1).
3. **Commands the backend sends by itself** (the auto-select countdown, a pre-selected route, the low-battery abort) move the mission to `error` instead, with `mission.error.code` `command_rejected` or `command_timeout` (§6.1).
4. **`system.ros` and `system.jetson` are live** with ROS sources (§3.2).
5. **Reset sim** also resets the ROS world (`world_node`) when `SOURCE_CART=ros` (§4.5).

**Changes in v1.4** (the final simulation world, [`docs/WORLD.md`](../docs/WORLD.md); ROS side in [`ROS_TOPICS.md`](./ROS_TOPICS.md) v2.0):

1. **Rescue during `arrived`:** new `mission.rescue` (`in_progress` → `patient_loaded`), broadcast as `mission.state` with `reason: "patient_loaded"` (§3.6, §6.1).
2. **Start return only once the patient is loaded:** `POST return` before that is `409 invalid_transition` (§4.4).
3. **The hub is the First Aid Center.** The return leg ends there (route reversed → start plaza → First Aid Center, §3.6), and `site.hub` is its door (§3.1).
4. **`completed` after the drone has landed on the cart.** The cart reaching the First Aid Center is no longer a transition (`reason: "cart_at_hub"`, §6.1).
5. **Route ids are the route names in `routes.yaml`:** `A_west`, `B_north`, `C_east` (§1).
6. **New `Route.label`**, localised text: `{ "en": "C · East", "ar": "C · شرق" }`, from the backend's `ROUTE_LABELS` map. The UI shows the label in the current language, never the raw id (§3.7).
7. **`Route.score` is derived by the backend:** the fastest `estTimeS` divided by the route's own `estTimeS`, so the fastest route scores 1.0. Node 2 only produces `estimatedTimeS` (§3.7).
8. **`Route.crowdFactor` is nullable** (Node 2 has none) (§3.7).
9. **Projection values set** for the final world: the site bounds fill the map, north up (§2).
10. **The incident is always the world incident** at Gazebo (0, 112). Posted coordinates are accepted but not used (§4.3).
11. **`scanAltitudeM` is 8** (the scan is 3 headings from above the cart, not a 60 m climb and 360° turn), and `MOCK_BEST_ROUTE` takes a route id (§7).
12. **New `Mission.returnPolyline`:** the return leg of the selected route (route reversed from where the cart stops → start plaza → First Aid Center), projected, set when the route is locked in. The UI draws the return line from it (§3.6).
13. **New `MissionSummary.routeLabel`** (localised text, like `Route.label`), so the "Recent" list shows "C · East" / "C · شرق" for closed missions (§3.8).

ROS topic details are in [`ROS_TOPICS.md`](./ROS_TOPICS.md). For nodes that are already implemented, [`ros_samples.md`](./ros_samples.md) is the ground truth and wins over both documents.

This contract replaces the mock layer in the frontend:

| Frontend today | Replaced by |
| --- | --- |
| `src/data/mockData.js` (static data) | REST endpoints (§4) |
| `src/api/dashboardApi.js` (returns mocks as Promises) | `fetch('/api/...')` + one WebSocket (§5) |
| `src/composables/useMission.js` (client-side simulation: `sp`, `hold`, `p`, timers) | Server-owned mission state machine (§6) and live WS messages |

### v1 system shape

```
Gazebo + ros_gz_bridge ─┐
yolo_node (Node 1) ─────┤
autonomy_node (Node 2) ─┤  ROS 2 topics   ┌────────────────┐  REST + WS   ┌───────────┐
mission_node (Node 3) ──┼───────────────▶ │ backend_bridge │ ───────────▶ │ dashboard │
world_node ─────────────┘ ◀────────────── │ (this backend) │ ◀─────────── │  (Vue)    │
                                           └────────────────┘              └───────────┘
```

- **One drone (`SBQ-01`) and one cart (`CART-01`)** per system. The API keeps arrays (`/drones`, `/carts`), but they always hold exactly one item.
- **The drone's dock is on top of the golf cart.** The drone launches from the cart and lands back on it.
- **ROS owns the decisions** about routes, flight and driving. The backend relays ROS data, owns the mission state machine, runs the 3 s auto-select countdown and forwards operator commands to ROS.
- **There is no database.** All state lives in memory, and only the last 10 closed missions are kept.

---

## 1. Conventions

- **Base URL:** `/api` for REST and `/ws` for the WebSocket. Both are served from the same origin, and Vite proxies them in development.
- **JSON** everywhere, with `camelCase` keys.
- **Raw values, not display strings.** Numbers carry the unit in the key name (`lengthM`, `estTimeS`, `speedKmh`, `batteryPct`, `altitudeM`, `headingDeg`). The frontend formats them.
- **Times** are ISO 8601 UTC strings, e.g. `"2026-10-04T14:02:11.250Z"`. Durations are in seconds.
- **ROS stamps are sim time**, i.e. seconds since the simulation started (`sec: 44`), not wall-clock time. They are **never** converted to ISO dates. Every ISO field that comes from ROS data (`ts`, `frameStamp`, …) is the **backend's receive time**. Where the raw ROS stamp is useful, it is passed through as `simTimeS` (a float, `sec + nanosec·1e-9`).
- **Localised text** is an object `{ "en": "...", "ar": "..." }`. It is used for data that comes from incidents and for route labels (`Route.label`, `MissionSummary.routeLabel`). Either key may be `null`; the UI falls back to the other one. Static UI copy stays in `translations.js`.
- **IDs:** missions use `MSN-0418`, incidents use `INC-0419`, the drone is `SBQ-01`, and the cart is `CART-01`.
- **Routes are a dynamic list.** Their ids are the route names in `routes.yaml`, the file Node 2 scores from (v1.4: `"A_west"`, `"B_north"` and `"C_east"`). People see `Route.label`, never the id. Neither the API nor the frontend may hard-code route ids or route count. Wherever a route id appears, it must be one of `mission.routes[].id`. Wherever per-route values are keyed, they are an object keyed by route id (e.g. `counts: { "A_west": 18, "B_north": 26, "C_east": 10 }`).
- **No authentication in v1** (§8).

### 1.1 Errors

Every non-2xx response, **including validation errors**, uses this envelope:
```json
{ "error": { "code": "invalid_transition", "message": "Cannot launch from state 'escorting'", "details": {} } }
```

| Status | `code` | When |
| --- | --- | --- |
| 400 | `bad_request` | Validation failed. `details.errors` lists the field errors. |
| 404 | `not_found` | Unknown id |
| 409 | `invalid_transition` | The command is not allowed in the current mission state |
| 409 | `mission_active` | An incident cannot be activated because a mission is running |
| 409 | `command_rejected` | A ROS node refused the command (ROS sources only). `details: { "command": "start_return", "node": "world_node", "reason": "patient_not_loaded" }`. The mission keeps its state. |
| 503 | `drone_unavailable` | The drone is not docked or not healthy |
| 503 | `ros_unavailable` | backend_bridge has lost contact with ROS |
| 504 | `ros_timeout` | A ROS node did not acknowledge the command within 3 s (ROS sources only). `details: { "command": "launch", "nodes": ["mission_node", "world_node"], "timeoutS": 3.0 }`. The mission keeps its state. |

Implementation note (FastAPI): register custom exception handlers for `RequestValidationError`, which by default returns 422 and must be mapped to **400 `bad_request`**, and for `HTTPException`. Both must produce the envelope above, so no response ever uses FastAPI's default `{"detail": ...}` body.

### 1.2 Position

Every position carries **both** GPS and SVG map coordinates. The backend computes both from Gazebo local metres using §2.

```json
{ "lat": 24.959206, "lon": 46.6495, "x": 500, "y": 134.5 }
```

Route polylines are arrays of positions. All lat/lon numbers in this document are illustrative.

---

## 2. Coordinate projection

Gazebo publishes positions in **local metres** in an ENU frame. The backend projects them into lat/lon and SVG `x`/`y` from a single reference point. The values fit the final world's site bounds (`docs/WORLD.md` §2: x −95…95 m, y −32…150 m, i.e. 190 × 182 m) into the `1000 × 640` viewBox, north up: 182 m × 3.5 = 637 units of the 640 high, centred on both axes. They are the backend defaults (`SABEQ_PROJ_*`).

| Parameter | Value | Meaning |
| --- | --- | --- |
| Reference point, Gazebo | `(E, N) = (0, 0)` m | World origin: the start plaza, where the cart starts |
| Reference point, GPS | `lat0 = 24.9582`, `lon0 = 46.6495` | **Placeholder.** The simulated world has no real location; any anchor works as long as everyone uses the same one. |
| Reference point, SVG | `x0 = 500`, `y0 = 526.5` | Where the Gazebo origin sits in the `1000 × 640` viewBox |
| Scale | `s = 3.5` SVG units per metre | The 100 m scale bar (`site.map.scaleBarM`) is 350 units long |
| Map rotation | `θ = 0` degrees | SVG up is north |

Reference points (SVG `x`, `y`):

| World point (E, N) | SVG |
| --- | --- |
| Site corner north-west (−95, 150) | (167.5, 1.5) |
| Site corner south-east (95, −32) | (832.5, 638.5) |
| Start plaza / cart start (0, 0) | (500, 526.5) |
| First Aid Center door (8.6, −15) — `site.hub` | (530.1, 579) |
| First Aid Center cart stop (0, −15) | (500, 579) |
| Incident (0, 112) | (500, 134.5) |

**Axis directions**

| Frame | +first axis | +second axis |
| --- | --- | --- |
| Gazebo (ENU) | `E` = east | `N` = north (`z` is up and ignored for 2-D) |
| GPS | `lon` increases to the east | `lat` increases to the north |
| SVG | `x` increases to the right | `y` increases **downward** |

**Formulas** (equirectangular, which is accurate to well under 1 m across a site of a few kilometres)

```
lat = lat0 + N / 111_320
lon = lon0 + E / (111_320 · cos(lat0))

E' =  E·cos θ − N·sin θ          # rotate into map-up frame
N' =  E·sin θ + N·cos θ
x  = x0 + s · E'
y  = y0 − s · N'                 # minus: SVG y points down
```

The inverse is derived by reversing the steps above (it is needed for the v2 cart position from the operator tablet's GPS). Gazebo poses should also be exposed as `localM: { e, n }` in debug logs, so the projection can be checked against the reference points above.

---

## 3. Data models

### 3.1 Site — `Site`
```json
{
  "id": "expo-2030",
  "name": { "en": "Expo 2030 Riyadh", "ar": "إكسبو 2030 الرياض" },
  "visitors": 41280,
  "map": { "viewBox": { "w": 1000, "h": 640 }, "scaleBarM": 100 },
  "hub": { "lat": 24.958065, "lon": 46.649585, "x": 530.1, "y": 579 },
  "zones": ["A", "B", "C", "D"]
}
```
- `visitors` is the number of visitors on site, or `null` when it isn't configured (`SITE_VISITORS`). There is no live source in v1, and the UI shows "—" for `null`.
- `hub` is the **First Aid Center** (the EMS hub), at its door (8.6, −15). The cart is parked beside it, and the return leg ends at its door, where the medics carry the patient inside. Missions start and end there.
- Static label placement (zones, landmarks, gates) stays in `siteMap.js`.

### 3.2 System status — `SystemStatus` (header "beats")
```json
{
  "ros": { "ok": true, "label": "OK" },
  "jetson": { "ok": true, "fps": 24 },
  "backend": { "ok": true, "uptimeS": 18234 }
}
```
- `ros.ok` is `false` when no `/drone/status` message has arrived for 5 s (`SOURCE_DRONE=ros`). With a ROS camera but a mock drone, `/camera/image` frames are the heartbeat instead. With all-mock sources both values stay fixed.
- `jetson.fps` is the measured rate of `/person_detections` over the last 5 s, as received by `backend_bridge` (`ok` while it is above 0).
- WebSocket latency (the `WS 38 ms` chip) is measured **client-side** with `ping`/`pong` (§5.3).

### 3.3 Drone — `Drone`

Sources: `/drone/status` and `/drone/position`.
```json
{
  "id": "SBQ-01",
  "state": "docked",
  "batteryPct": 100,
  "flightTimeLeftS": 1560,
  "position": { "lat": 24.9582, "lon": 46.6495, "x": 500, "y": 526.5 },
  "altitudeM": 0,
  "headingDeg": 0,
  "speedMps": 0,
  "dockedOnCart": true,
  "payload": { "camera": true, "speaker": true, "led": true },
  "detector": { "model": "YOLOv8n", "fps": 24 },
  "link": "ok"
}
```
- `state` takes one of these values: `docked | taking_off | scanning | escorting | returning_to_dock | landing | fault | offline`
- `dockedOnCart` is `true` while the drone sits on the cart dock. In that case `position` equals the cart's position.
- `payload` lists the hardware that is present and healthy. The speaker and LED *on/off* state lives on the mission (§3.6).
- `link` takes one of these values: `ok | degraded | lost`

### 3.4 Cart — `Cart`

Sources: `/cart/pose` and `/cart/status`.
```json
{
  "id": "CART-01",
  "position": { "lat": 24.9582, "lon": 46.6495, "x": 500, "y": 526.5 },
  "headingDeg": 0,
  "speedKmh": 0,
  "state": "at_hub"
}
```
- `state` takes one of these values: `at_hub | to_incident | at_incident | to_hub | stopped`
- **In v1 the position comes from Gazebo (`/cart/pose`).** In v2 it will come from the GPS of the operator's tablet, because the operator rides in the cart (§8).

### 3.5 Incident — `Incident`
```json
{
  "id": "INC-0419",
  "status": "active",
  "severity": "critical",
  "type": { "code": "cardiac_arrest", "label": { "en": "Suspected cardiac arrest", "ar": "اشتباه توقف القلب" } },
  "location": {
    "zone": "B",
    "description": { "en": "Zone B · near Pavilion B-14", "ar": "المنطقة ب · قرب الجناح B-14" },
    "position": { "lat": 24.959206, "lon": 46.6495, "x": 500, "y": 134.5 }
  },
  "source": "staff_radio",
  "reportedAt": "2026-10-04T14:02:00Z",
  "distanceFromHubM": 127.3,
  "missionId": "MSN-0418"
}
```
- `status` takes one of these values: `queued | active | resolved | transferred | cancelled | aborted | error`
- `severity` takes one of these values: `critical | high | medium | low`
- `source` takes one of these values: `staff_radio | dispatch | sensor | operator`. `operator` means the incident was entered manually in the dashboard.
- `missionId` is `null` while the incident is `queued`.
- When an incident becomes active, the backend publishes it to ROS on `/incident`.

### 3.6 Mission — `Mission`
```json
{
  "id": "MSN-0418",
  "incidentId": "INC-0419",
  "droneId": "SBQ-01",
  "cartId": "CART-01",
  "state": "escorting",
  "stateEnteredAt": "2026-10-04T14:03:23Z",
  "scanAltitudeM": 8,
  "scan": { "phase": "done", "progress": 1, "coveragePct": 100 },
  "routes": [ /* Route, see 3.7 */ ],
  "recommendedRoute": "C_east",
  "preselectedRoute": null,
  "selectedRoute": "C_east",
  "selectionSource": "auto",
  "autoSelectAt": "2026-10-04T14:03:23Z",
  "escort": { "leg": "outbound", "progress": 0.12, "elapsedS": 15, "etaS": 110, "droneAheadOfCartM": 40 },
  "returnPolyline": [
    { "lat": 24.959206, "lon": 46.649550, "x": 517.5, "y": 134.5 },
    { "lat": 24.959206, "lon": 46.649659, "x": 556, "y": 134.5 },
    { "lat": 24.959026, "lon": 46.649857, "x": 626, "y": 204.5 },
    { "lat": 24.958523, "lon": 46.649857, "x": 626, "y": 400.5 },
    { "lat": 24.9582, "lon": 46.6495, "x": 500, "y": 526.5 },
    { "lat": 24.958065, "lon": 46.6495, "x": 500, "y": 579 }
  ],
  "payload": { "speaker": true, "led": true },
  "rescue": null,
  "timeline": [
    { "state": "incident_received", "at": "2026-10-04T14:02:00Z" },
    { "state": "taking_off", "at": "2026-10-04T14:03:05Z" },
    { "state": "scanning", "at": "2026-10-04T14:03:10Z" },
    { "state": "route_selected", "at": "2026-10-04T14:03:20Z" },
    { "state": "escorting", "at": "2026-10-04T14:03:23Z" }
  ],
  "result": null,
  "outcome": null,
  "abortReason": null,
  "error": null,
  "createdAt": "2026-10-04T14:02:00Z",
  "endedAt": null
}
```
- `scan.phase` takes one of these values: `pending | climb | rotate | done`
- `recommendedRoute` is relayed from ROS (`/selected_route`). The backend never computes it.
- `recommendedRoute` and `selectedRoute` are route ids from `routes[].id`.
- `preselectedRoute` is the operator's choice made **during the scan** with `POST route`, or `null`. It is a route id from `routes[].id`. When the recommendation arrives, the mission goes straight on to `escorting` on that route (`selectionSource: "operator"`, no countdown). It keeps its value afterwards, so the UI can show that the route was pre-selected. If a new route list no longer has that id, it is reset to `null`.
- `selectionSource` takes one of these values: `auto | operator`, or `null` before a route is selected.
- `autoSelectAt` is set when the mission enters `route_selected` and equals entry time + 3 s. The UI derives its countdown from it.
- `escort.leg` is `outbound` (hub → incident, along the selected route) or `return` (incident → First Aid Center: the **same route reversed**, from where the cart stopped back to the start plaza, then to the First Aid Center door). It is `null` outside `escorting` and `returning`.
  - `progress` is the cart's distance along the leg's polyline divided by the polyline length. It runs from 0 to 1.
  - `elapsedS` is the time since the leg started.
  - `etaS = max(0, estTimeS − elapsedS)`, where `estTimeS` is the selected route's value from ROS.
  - `droneAheadOfCartM` is the distance along the route between the drone and the cart.
- `returnPolyline` is the **return leg** of the selected route, as positions: from where the cart stops (5 m before the incident) back along the route to the start plaza, then to the First Aid Center cart stop (`docs/WORLD.md` §7; the backend's `World.return_path`, which matches `demo_common.build_return_path`). It is `null` until a route is locked in (`escorting`), then fixed for the mission. `escort.progress` on the return leg is measured along this line, and the UI draws the return line from it instead of reversing `routes[].polyline`.
- `rescue` tracks the medics at the incident. It is `null` until the mission enters `arrived`, then:
  ```json
  { "state": "in_progress", "patientLoadedAt": null }
  ```
  - `state` takes one of these values: `in_progress | patient_loaded`. It is set to `in_progress` on entering `arrived` (the medics leave the cart, treat the patient and load them onto the stretcher) and moves to `patient_loaded` when ROS reports the medics back on the cart with the patient. It never goes back.
  - `patientLoadedAt` is the backend's receive time of that report (ISO), or `null`.
  - The change is broadcast as `mission.state` with `from == to == "arrived"` and `reason: "patient_loaded"`.
  - It keeps its last value in `returning`, `completed` and in the history, and stays `null` for missions that never arrived.
- `timeline` gains an entry for every state entered, including `returning` and `completed`.
- `result` is filled in on `arrived`:
  ```json
  { "responseTimeS": 134, "vsBestAlternativeS": -86, "peopleWarned": 38, "droneBatteryPct": 89 }
  ```
  - `responseTimeS` is the time from `incident_received` to `arrived`.
  - `vsBestAlternativeS` is the selected route's `estTimeS` minus the **lowest** `estTimeS` among the other routes, using the values from ROS. It is negative when the selected route was faster. It replaces v1.0 `vsOtherRouteS`, which assumed exactly two routes.
  - `peopleWarned` is the **final YOLO person count on the selected route from the scan**, taken from the last `/route_scores` message before selection.
  - `droneBatteryPct` is the battery level at `arrived`.
- `outcome` is set on close. It takes one of these values: `resolved | transferred | cancelled | aborted | error`.
- `abortReason` takes one of these values: `operator_abort | return_to_dock | low_battery`, or `null`.
- `error` holds `{ "code": "link_lost", "message": "..." }` in the `error` state, and `null` otherwise.

### 3.7 Route — `Route`

Relayed from ROS `/route_scores` (Node 2: `distanceM`, `peopleCount`, `estimatedTimeS`). The backend adds the label, the projected `polyline` and the `score`.
```json
{
  "id": "C_east",
  "label": { "en": "C · East", "ar": "C · شرق" },
  "lengthM": 151.2,
  "peopleCount": 10,
  "countFinal": true,
  "crowdFactor": null,
  "estTimeS": 26.6,
  "score": 1.0,
  "polyline": [
    { "lat": 24.9582, "lon": 46.6495, "x": 500, "y": 526.5 },
    { "lat": 24.958523, "lon": 46.649857, "x": 626, "y": 400.5 },
    { "lat": 24.959026, "lon": 46.649857, "x": 626, "y": 204.5 },
    { "lat": 24.959206, "lon": 46.649659, "x": 556, "y": 134.5 },
    { "lat": 24.959206, "lon": 46.6495, "x": 500, "y": 134.5 }
  ]
}
```
- `mission.routes` is the full list in `routes.yaml` order, which is also the scan order (v1.4: A_west, B_north, C_east). The frontend renders one card and one polyline per entry.
- `label` is the display name, as localised text `{ "en", "ar" }`, from the backend's `ROUTE_LABELS` map keyed by route id (defaults: `A_west` → A · West / A · غرب, `B_north` → B · North / B · شمال, `C_east` → C · East / C · شرق). The frontend shows it in the current language wherever a route is named (cards, map labels, sector chips, buttons, history) and **never shows the raw `id`**.
- `polyline` is the cart's path: the cart start → the route → the incident (`routes.yaml` waypoints).
- `estTimeS` is Node 2's `estimatedTimeS` (`distance / 7.0 + people × 0.5`). It is `null` until Node 2 publishes its scores at the end of the scan. The frontend shows `--:--` while it is `null`.
- `score` is **derived by the backend** from `estTimeS`: the fastest route's `estTimeS` divided by this route's, so the fastest route scores **1.0** and slower routes score less (e.g. 26.6 / 30.6 = 0.869). It is `null` while `estTimeS` is `null`.
- `crowdFactor` is `null`: Node 2 does not produce one. The field is kept for a future scorer.
- `peopleCount` grows during the scan and is final once `countFinal` is `true`.

### 3.8 Mission history item — `MissionSummary` (the "Recent" list)
```json
{
  "id": "MSN-0417",
  "reportedAt": "2026-10-04T13:41:00Z",
  "incidentType": { "code": "fall_injury", "label": { "en": "Fall injury", "ar": "إصابة سقوط" } },
  "zone": "C",
  "route": "C_east",
  "routeLabel": { "en": "C · East", "ar": "C · شرق" },
  "responseTimeS": 151,
  "droneId": "SBQ-01",
  "outcome": "resolved"
}
```
- `outcome` takes one of these values: `resolved | transferred | cancelled | aborted | error`
- `route`, `routeLabel`, `responseTimeS` and `droneId` are `null` when the mission never reached that point.
- `routeLabel` is the selected route's `label` at close (localised text). The UI shows it in the current language, never `route` (the id).
- Only the **last 10** closed missions are kept, in memory, newest first. They are lost when the backend restarts.

### 3.9 Camera — `CameraInfo` and `Detections`
```json
{
  "droneId": "SBQ-01",
  "available": true,
  "streamUrl": "/api/drones/SBQ-01/camera/stream",
  "protocol": "mjpeg",
  "mode": "forward",
  "resolution": { "w": 1280, "h": 960 }
}
```
- The backend serves the stream as MJPEG by encoding the **raw** Gazebo frames from `/camera/image` (1280×960, `rgb8`, which is 4:3). Frames are converted RGB → BGR before OpenCV JPEG encoding.
- `resolution` is taken from the latest `/camera/image` message.
- `/yolo/annotated_image` is **not used in v1**. It publishes at about 0.2–0.34 Hz, which is too slow for streaming (`ros_samples.md` §5). The frontend draws the boxes itself.
- `mode` takes one of these values: `off | scan_360 | forward`

YOLO detection boxes (`/person_detections`, a `vision_msgs/msg/Detection2DArray`) are converted by `backend_bridge` and pushed over WS as `camera.detections` (§5.2). Box coordinates are **percent of the frame (0–100)**, with `x`/`y` at the **top-left** corner:
```json
{ "x": 77.94, "y": 60.52, "w": 0.98, "h": 4.78, "conf": 0.806, "cls": "person" }
```
- The conversion is specified in `ROS_TOPICS.md` §4.2, and its worked example is `ros_samples.md` §1.
- Boxes carry **no route**. yolo_node only detects people. Attributing people to routes is Node 2's job (the count while each route is scanned), and the results arrive as `counts` per route.

---

## 4. REST endpoints

All endpoints are prefixed with `/api`. Mission commands are **REST only**. Their effects are broadcast over WS, and where needed they are forwarded to ROS on `/mission/command` or `/drone/payload_cmd`.

### 4.1 Bootstrap

| Method | Path | Response |
| --- | --- | --- |
| GET | `/snapshot` | Everything the dashboard needs on load or after a reconnect |

```json
{
  "site": { /* Site */ },
  "system": { /* SystemStatus */ },
  "drones": [ /* Drone — exactly one */ ],
  "carts": [ /* Cart — exactly one */ ],
  "activeMission": { /* Mission */ },
  "activeIncident": { /* Incident */ },
  "openIncidents": { "active": 1, "queued": 1 },
  "recentMissions": [ /* MissionSummary, up to 10, newest first */ ],
  "camera": { /* CameraInfo */ },
  "serverTime": "2026-10-04T14:03:21Z"
}
```
- `activeMission` and `activeIncident` are `null` when the system is idle.
- The header alert count is `openIncidents.active + openIncidents.queued`.

### 4.2 Site, system, fleet

| Method | Path | Response |
| --- | --- | --- |
| GET | `/site` | `Site` |
| GET | `/system/status` | `SystemStatus` |
| GET | `/drones` | `Drone[]` (one item) |
| GET | `/drones/{droneId}` | `Drone` |
| GET | `/drones/{droneId}/camera` | `CameraInfo` |
| GET | `/drones/{droneId}/camera/stream` | `multipart/x-mixed-replace; boundary=frame` (MJPEG from `/camera/image`) |
| GET | `/carts` | `Cart[]` (one item) |
| GET | `/carts/{cartId}` | `Cart` |

### 4.3 Incidents

| Method | Path | Request | Response |
| --- | --- | --- | --- |
| GET | `/incidents?status=active,queued` | — | `Incident[]` |
| GET | `/incidents/{incidentId}` | — | `Incident` |
| POST | `/incidents` | `CreateIncident` | `201` with `Incident` |

`CreateIncident` is used both by external systems and by operator manual entry (`source: "operator"`):
```json
{
  "severity": "critical",
  "type": "cardiac_arrest",
  "title": { "en": "Suspected cardiac arrest", "ar": "اشتباه توقف القلب" },
  "location": {
    "zone": "B",
    "description": { "en": "Zone B · near Pavilion B-14", "ar": "المنطقة ب · قرب الجناح B-14" },
    "lat": 24.959206,
    "lon": 46.6495
  },
  "source": "staff_radio",
  "reportedAt": "2026-10-04T14:02:00Z"
}
```
- `location` needs **either** `lat`/`lon` **or** `x`/`y`, but **the position is always the world incident** at Gazebo (0, 112) (`docs/WORLD.md` §2): the simulated world has one fixed incident, the end of every route. The posted coordinates are validated and then ignored; `zone` and `description` are kept as sent. `distanceFromHubM` is the straight line from the First Aid Center door to the incident (127.3 m).
- `reportedAt` is optional and defaults to the time the server receives the request.
- **Effect:** if the system is idle, the incident becomes `active`, a mission is created in `incident_received`, `/incident` is published to ROS, and the WS receives `incident.created` and then `mission.state`. Otherwise the incident becomes `queued` and the WS receives `incident.created` only.

### 4.4 Missions

| Method | Path | Request | Response | Allowed in state | ROS side effect |
| --- | --- | --- | --- | --- | --- |
| GET | `/missions/active` | — | `200` with `Mission`, or **`200` with `null`** when idle | any | — |
| GET | `/missions/history` | — | `MissionSummary[]` (≤ 10, newest first) | any | — |
| GET | `/missions/{missionId}` | — | `Mission` | any | — |
| POST | `/missions/{missionId}/launch` | `{}` | `200` with `Mission` | `incident_received` | `/mission/command` `launch` |
| POST | `/missions/{missionId}/dismiss` | `{}` | `200` with `Mission` | `incident_received` | — |
| POST | `/missions/{missionId}/route` | `{ "route": "C_east" }` (must be in `mission.routes[].id`, else `400`) | `200` with `Mission` | `scanning` (pre-select), `route_selected` | `/mission/command` `select_route` |
| POST | `/missions/{missionId}/payload` | `{ "speaker": false }` and/or `{ "led": true }` | `200` with `{ "speaker": bool, "led": bool }` | `taking_off` … `returning` | `/drone/payload_cmd` |
| POST | `/missions/{missionId}/abort` | `{ "reason": "operator_abort" \| "return_to_dock" }` | `200` with `Mission` | `taking_off`, `scanning`, `route_selected`, `escorting`, `returning` | `/mission/command` `abort` |
| POST | `/missions/{missionId}/return` | `{}` | `200` with `Mission` | `arrived`, **and** `rescue.state == "patient_loaded"` | `/mission/command` `start_return` |
| POST | `/missions/{missionId}/close` | `completed`: `{ "outcome": "resolved" \| "transferred" }`; `aborted` / `error`: `{}` | `200` with `Mission` | `completed`, `aborted`, `error` | — |

- `/missions/history` must be registered **before** `/missions/{missionId}` in the router.
- **With ROS sources**, `launch`, `route` (from `route_selected`), `abort` and `return` publish the command and wait up to 3 s for every acting node's `command_ack` (`ROS_TOPICS.md` §4.11) before the transition and the reply. A refusal returns `409 command_rejected`, no ack `504 ros_timeout`; in both cases the mission keeps its state. The UI keeps the button pending until the reply arrives. A `route` during `scanning` (pre-select) does not publish anything, so it replies at once.
- `return` in `arrived` before the patient is loaded (`rescue.state` is `in_progress`) returns **`409 invalid_transition`** with the message `Cannot return before the patient is loaded`.
- `close` from `aborted` or `error` ignores any `outcome` in the body. The outcome is set to `aborted` or `error` respectively.
- `route` during `scanning` sets `preselectedRoute`, without a state change. The route list (`mission.routes`) is filled by the route scores during the scan, so pre-selection is possible once the first `route.scores` has arrived. Before that, the request returns `400`.

How these map to buttons (the full UI work list is in `FRONTEND_TODO.md`):

| UI button | Call |
| --- | --- |
| Launch | `POST /missions/{id}/launch` |
| False alarm | `POST /missions/{id}/dismiss` |
| Use route &lt;id&gt; (override, new; one button per route) | `POST /missions/{id}/route` |
| Speaker / LED toggles | `POST /missions/{id}/payload` |
| Abort | `POST /missions/{id}/abort {"reason":"operator_abort"}` |
| Return to dock (was "Return to hub") | `POST /missions/{id}/abort {"reason":"return_to_dock"}` |
| Start return (new, on `arrived`; **enabled only when `mission.rescue.state == "patient_loaded"`**) | `POST /missions/{id}/return` |
| Close incident: Resolved / Transferred (on `completed`) | `POST /missions/{id}/close` |
| Close (on `aborted` / `error`) | `POST /missions/{id}/close` |
| Manual incident entry (new) | `POST /incidents {"source":"operator", ...}` |

### 4.5 Dev endpoints (only when `DEV_MODE=true`)

These endpoints are not mounted otherwise, so they return `404 not_found`. They are meant for demos and rehearsals with the mock data sources.

| Method | Path | Request | Response | Effect |
| --- | --- | --- | --- | --- |
| POST | `/dev/sim/reset` | — | `200` with `Snapshot` | Back to the boot state. With `SOURCE_CART=ros`, `world_node` also puts the cart, crowd, medics and patient back (`/mission/command` `reset`, no ack awaited). There is no active mission, the queue and history are empty, and ids restart at `INC-0001` / `MSN-0001`. The drone is docked on the cart at the hub, every data source is reset, and the countdown timer is cancelled. Every WS client receives a fresh `snapshot`. |

---

## 5. WebSocket — `/ws`

A single connection carries all push traffic. Every message in both directions uses this envelope:

```json
{ "type": "drone.telemetry", "data": { }, "ts": "2026-10-04T14:03:21.120Z" }
```

### 5.1 Connection lifecycle

1. The client connects to `/ws`.
2. The server immediately sends `snapshot`, whose `data` has the same shape as `GET /api/snapshot`.
3. The server then streams the messages below.
4. On reconnect the client waits for the new `snapshot` and replaces its local state.
5. The client pings every 5 s. The server closes connections that have sent no `ping` for 30 s.

### 5.2 Server → client

| `type` | Source | When / rate | `data` |
| --- | --- | --- | --- |
| `snapshot` | backend | On connect | See §4.1 |
| `system.status` | backend | Every 2 s and on change | `SystemStatus` |
| `incident.created` | REST | New incident | `{ "incident": Incident, "openIncidents": {...} }` |
| `incident.updated` | backend | Status change | `{ "incident": Incident, "openIncidents": {...} }` |
| `mission.state` | backend | **Every** state transition; also with `from == to` when a mission field changes without a transition (`reason: "preselected"`, `"patient_loaded"`, `"cart_at_hub"`) | `{ "missionId", "from", "to", "reason": string \| null, "mission": Mission }` |
| `drone.telemetry` | `/drone/status` + `/drone/position` | ≤ 5 Hz | `{ "droneId", "state", "batteryPct", "flightTimeLeftS", "position", "altitudeM", "headingDeg", "speedMps", "dockedOnCart", "link" }` |
| `cart.telemetry` | `/cart/pose` + `/cart/status` | ≤ 5 Hz | `{ "cartId", "state", "position", "headingDeg", "speedKmh" }` |
| `scan.progress` | `/mission_events` + `/drone/position` | ≤ 5 Hz during `taking_off` and `scanning` | `{ "missionId", "phase", "progress", "altitudeM", "headingDeg", "coveragePct" }` |
| `route.scores` | `/route_scores` (relayed) | Each ROS message during `scanning` | `{ "missionId", "routes": Route[], "final": false }` |
| `route.recommendation` | `/selected_route` (relayed) | On entering `route_selected` | `{ "missionId", "recommended": "C_east", "routes": Route[], "autoSelectAt": iso, "countdownS": 3 }` |
| `route.selected` | backend | Route locked in (auto or override) | `{ "missionId", "route": "C_east", "source": "auto"\|"operator" }` |
| `escort.progress` | backend, from `/cart/pose` + `/drone/position` | 2 Hz during `escorting` and `returning` | `{ "missionId", "leg": "outbound"\|"return", "progress", "elapsedS", "etaS", "droneAheadOfCartM" }` |
| `payload.state` | `/drone/status` | On change | `{ "missionId", "speaker": true, "led": false }` |
| `camera.detections` | `/person_detections` (converted, see ROS_TOPICS §4.2) | Each message, throttled to ≤ 10 Hz | `{ "droneId", "missionId", "mode", "headingDeg": number \| null, "frameStamp": iso (backend receive time), "simTimeS": 44.784, "imageSize": { "w": 1280, "h": 960 }, "boxes": Box[], "counts": { "A_west": 18, "B_north": 26, "C_east": 10 } \| null }` |
| `camera.info` | backend | Mode change | `CameraInfo` |
| `mission.history` | backend | A mission closes | `{ "item": MissionSummary }` |
| `error` | backend | Async failure | `{ "code", "message", "missionId": string \| null }` |
| `pong` | backend | Reply to `ping` | `{ "clientTs": "<echoed>" }` |

- The v0.1 `route.counts` message is renamed `route.scores`. It now relays the full ROS route data instead of counts only.
- The envelope `ts` is the backend's send time, never a ROS sim stamp.
- In `camera.detections`, `counts` is copied from the latest `/route_scores`. It is `null` outside the scan. During the scan the backend mirrors Node 2's live per-route counts (`ROS_TOPICS.md` §6.1). `headingDeg` is taken from the latest `/drone/position`, and it is `null` until that topic exists.
- v1.0's `frameTs` is renamed `frameStamp`.

### 5.3 Client → server

| `type` | `data` | Purpose |
| --- | --- | --- |
| `ping` | `{ "clientTs": iso }` | Keepalive, and the round trip used for the `WS xx ms` chip |

Commands are **not** sent over WS. Use REST (§4.4).

---

## 6. Mission state machine

There is one mission at a time. Incidents that arrive during a mission wait in `queued` and are activated, in arrival order, when the mission is closed.

```
 ┌──────┐  incident activated   ┌───────────────────┐  launch   ┌────────────┐
 │ idle │ ────────────────────▶ │ incident_received │ ────────▶ │ taking_off │
 └──────┘ ◀─────── dismiss ──── └───────────────────┘           └────────────┘
    ▲                                                                 │ scan altitude reached
    │                                                                 ▼
    │                                                           ┌────────────┐
    │                                                           │  scanning  │
    │                                                           └────────────┘
    │                                                                 │ /selected_route from ROS
    │                                                                 ▼
    │                                                         ┌────────────────┐
    │                                                         │ route_selected │
    │                                                         └────────────────┘
    │                                         3 s countdown expires   │ or operator override
    │                                                                 ▼
    │                                                           ┌────────────┐
    │                                                           │ escorting  │  outbound leg
    │                                                           └────────────┘
    │                                                                 │ cart at incident
    │                                                                 ▼
    │                                                           ┌────────────┐
    │                                                           │  arrived   │
    │                                                           └────────────┘
    │                                    rescue: in_progress → patient_loaded
    │                                                                 │ POST /return (patient loaded)
    │                                                                 ▼
    │                                                           ┌────────────┐
    │                                                           │ returning  │  return leg
    │                                                           └────────────┘
    │                          cart at First Aid Center, then drone   │ landed on the cart
    │            close (resolved | transferred)                       ▼
    ├──────────────────────────────────────────────────────── ┌────────────┐
    │                                                         │ completed  │
    │                                                         └────────────┘
    │            close                                     ┌───────────┐
    ├───────────────────────────────────────────────────── │  aborted  │ ◀── abort from taking_off,
    │                                                      └───────────┘     scanning, route_selected,
    │            close                                     ┌───────────┐     escorting or returning
    └───────────────────────────────────────────────────── │   error   │ ◀── fault from any non-idle state
                                                           └───────────┘
```

**Transition rule:** transitions caused by an operator command happen in the backend **as soon as** the command is accepted and forwarded to ROS. Transitions caused by physical milestones happen when the matching ROS message arrives. `ROS_TOPICS.md` lists which message triggers which transition.

### 6.1 Transitions

| From | To | Trigger | Side effects |
| --- | --- | --- | --- |
| `idle` | `incident_received` | An incident is created while idle, or the next `queued` incident is activated on close | Create the mission, set incident `active`, publish `/incident`. Send `incident.updated` and `mission.state`. |
| `incident_received` | `idle` | `POST dismiss` | Incident `cancelled`, mission `outcome=cancelled`, a `mission.history` item, then activate the next queued incident |
| `incident_received` | `taking_off` | `POST launch` | Rejected with `503 drone_unavailable` unless the drone is `docked` on the cart and healthy. Publishes `/mission/command` `launch`. |
| `taking_off` | `scanning` | ROS: scan altitude reached | Camera `scan_360`, `scan.phase=rotate` |
| `scanning` | `route_selected` | ROS: `/selected_route` (Node 2's pick, made right after its `/route_scores`) | Store `recommendedRoute` and freeze `peopleCount`. If the operator pre-selected a route, go straight on to `escorting`. Otherwise set `autoSelectAt = now + 3 s` and send `route.recommendation`. |
| `route_selected` | `escorting` | `autoSelectAt` reached (`source=auto`, route = `recommendedRoute`) **or** `POST route` (`source=operator`) | Publish `/mission/command` `select_route`. Send `route.selected`. `escort.leg=outbound`. The cart drives the selected route and the drone escorts ahead. Speaker and LED turn on. |
| `escorting` | `arrived` | ROS: cart `at_incident` (stopped at the incident) | Fill in `mission.result`. Set `rescue = { state: "in_progress" }`. The drone hovers behind the cart and watches the medics. |
| `arrived` | `arrived` (no transition) | ROS: patient loaded (the medics are back on the cart with the patient) | `rescue.state = "patient_loaded"`, `patientLoadedAt` set. Send `mission.state` (`reason: "patient_loaded"`). **Start return** becomes allowed. |
| `arrived` | `returning` | `POST return`, only when `rescue.state == "patient_loaded"` (else `409 invalid_transition`) | Publish `/mission/command` `start_return`. `escort.leg=return`. The cart turns and drives back along the **selected route reversed** to the start plaza, then to the **First Aid Center**, and the drone escorts ahead again. |
| `returning` | `returning` (no transition) | ROS: cart `at_hub` (stopped at the First Aid Center) | `escort.progress = 1`. Send `mission.state` (`reason: "cart_at_hub"`). The medics carry the patient inside, and the drone comes over the cart to land. |
| `returning` | `completed` | ROS: **the drone has landed on the cart** (`/mission_events` `landed_on_dock`) | The cart is at the First Aid Center and the drone is docked on it (`drone.dockedOnCart = true`). |
| `taking_off` / `scanning` / `route_selected` / `escorting` / `returning` | `aborted` | `POST abort` (`operator_abort`, `return_to_dock`), or a ROS low-battery event (`low_battery`) | Publish `/mission/command` `abort`. **The cart stops, and the drone flies back and lands on the cart dock.** The operator then drives the cart back to the hub manually, and the backend does not track this. |
| any non-idle | `error` | ROS: drone fault, `/drone/status` link `lost` or silent for > 5 s, a node failure reported on `/mission_events`, or a command the backend sent by itself (auto-select countdown, pre-selected route, low-battery abort) that a ROS node rejects or does not acknowledge within 3 s (`mission.error.code` `command_rejected` / `command_timeout`; `ROS_TOPICS.md` §4.11) | `mission.error` is set and an `error` message is sent. ROS attempts the return to the cart dock. |
| `completed` | `idle` | `POST close {"outcome":"resolved"\|"transferred"}` | Incident takes the same status, `outcome` is set, a `mission.history` item is added, and the next queued incident is activated |
| `aborted` | `idle` | `POST close` | Incident `aborted`, `outcome=aborted`, a `mission.history` item, the next queued incident is activated. **No requeue.** |
| `error` | `idle` | `POST close` (operator acknowledges) | Incident `error`, `outcome=error`, a `mission.history` item, the next queued incident is activated |

Any command sent in a state not listed for it returns `409 invalid_transition`.

With ROS sources, every transition triggered by a command (`launch`, `route`, `return`, `abort`, and the countdown) happens only once the ROS nodes have acknowledged it (§4.4); the side effect "Publish `/mission/command` …" comes first.

### 6.2 Frontend stage mapping

| Backend `state` | Frontend `stage` today | v1 frontend |
| --- | --- | --- |
| `idle` | `idle` | `idle` |
| `incident_received` | `alert` | `alert` |
| `taking_off` | `scan` ("Flying") | `scan`, `scan.phase=climb` |
| `scanning` | `scan` ("Hovering") | `scan`, with live `route.scores` |
| `route_selected` | `scan` with `scanDone` | `scan` with countdown from `autoSelectAt` and **one override button per route** |
| `escorting` | `escort` | `escort` (`leg=outbound`) |
| `arrived` | `arrived` | `arrived` with the rescue progress (`rescue.state`: medics treating → patient loaded) and a **Start return** button, **disabled until `patient_loaded`**. It replaces "Return SBQ-01 to hub" and "Close incident". |
| `returning` | — | **New** `returning` stage: the escort view with `leg=return`, the reversed route and the First Aid Center as the destination. After the cart reaches it, show "Drone landing on the cart" until `completed`. |
| `completed` | — | **New** `completed` stage (cart at the First Aid Center, drone landed on the cart): summary plus **Close: Resolved / Transferred** |
| `aborted` | — (jumps to `idle`) | **New** `aborted` stage: the drone's return to the dock (`drone.dockedOnCart`), plus **Close** |
| `error` | — | **New** `error` stage: error banner plus **Acknowledge & close** |

The `steps` timeline in `translations.js` gains `Returning` and `Completed` after `Arrived`.

---

## 7. Assumptions

- Incidents come from external systems and from operator entry, through one endpoint (`POST /incidents`) told apart by `source`.
- Positions carry both GPS and SVG coordinates. Projection parameters are in §2 (GPS anchor is a placeholder).
- One mission at a time. Queued incidents are activated in arrival order.
- Arrival at the incident and at the First Aid Center is decided by ROS (`world_node`: the cart stops 5 m before the incident centre, and at the First Aid Center stop). The backend only reacts to `/cart/status`. The rescue progress and the drone landing come from ROS too (`ROS_TOPICS.md` §4.10).
- `StageNav` demo jumps have no backend equivalent in v1. The frontend hides them when a backend is connected (see `FRONTEND_TODO.md`). For demos, use the mock data sources (`SOURCE_*=mock`, `MOCK_SPEED`, `MOCK_BEST_ROUTE`) and `POST /api/dev/sim/reset` (§4.5).

Decided after v1.0 review:
- **No queued-incident list in v1.** Queued incidents appear only in the header alert count (`openIncidents.queued`).
- **After `aborted`,** the drone lands on top of the stopped cart, and the cart operator drives back to the hub **manually**. The backend tracks no state for that drive. `cart.telemetry` keeps streaming from `/cart/pose`, so the cart still moves on the map.

---

## 8. Deferred to v2

| Item | v1 behaviour |
| --- | --- |
| Authentication (operator session/token, API key for external incident sources) | None. Run on a trusted network only. |
| Idempotent incident creation (`externalRef`) | Every POST creates a new incident |
| Pagination of mission history | Fixed list of the last 10 |
| Persistence / database | In memory. Lost on restart. |
| Multiple drones and carts | Exactly `SBQ-01` and `CART-01` |
| WebRTC / HLS camera streams | MJPEG only |
| Cart position from the operator tablet's GPS (the operator rides in the cart) | From Gazebo `/cart/pose` |
| Severity-ordered incident queue | Arrival order |
