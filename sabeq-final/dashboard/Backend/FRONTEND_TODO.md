# Frontend changes required by the API contract

Paths are relative to `Frontend/src/`.

- **v1.5 (ROS command acks): implemented.** Toast titles for `command_rejected`, `ros_timeout` and `command_timeout` (EN/AR, `data/translations.js`); the toast detail is the backend's message (it names the node and its reason). Buttons already stay pending until the REST reply, which with ROS comes after the acks (up to 3 s); `api/http.js` has no client timeout.
- **v1.4 (final world): implemented.** Section 0 below (checked end to end against the mock backend in EN and AR).
- **v1.3: implemented.** Sections 1–6 are kept as a record of what changed (the frontend is wired to the backend over REST + WS; see `Frontend/README.md`).

## 0. v1.4 — the final world (implemented)

Contract changes are listed at the top of [`API_CONTRACT.md`](./API_CONTRACT.md); world facts are in [`docs/WORLD.md`](../docs/WORLD.md). The mock backend runs the whole v1.4 flow (`MOCK_SPEED=5` for a quick look).

### 0.1 Route labels (never show the raw id)

`label` and `MissionSummary.routeLabel` are **localised text** `{en, ar}` (e.g. "C · East" / "C · شرق"): render them with `l()` like the incident texts.

Route ids are now `A_west`, `B_north`, `C_east`. Every place a route is named shows **`route.label`** ("A · West", "B · North", "C · East").

- [x] `composables/useMissionView.js`: `routeName(id)` currently formats `t.routeName` ("Route {id}") → would show "Route A_west". Return the route's `label` instead (look it up in `mission.routes`). This covers the route cards (`routeCards[].name`), the pre-select / confirm / "Use … instead" buttons and the auto-selected text.
- [x] `useMissionView.js` map labels: `label: \`${r.id} · ${fmtDistance(r.lengthM)}\`` → `${r.label} · …`.
- [x] `components/alerts/CameraFeed.vue`: the per-route count chips show `{{ c.id }} · {{ c.n }}` → show the label.
- [x] `translations.js`: drop `routeName`, `locRoute` ("Route B") and `locRouteA` if unused afterwards.
- [x] Keep using `id` for keys, `variants[...]`, colours (by index) and the `POST route` body.
- [x] The "Recent" list (`components/alerts/RecentList.vue`): show `MissionSummary.routeLabel` (new in v1.4; `null` when no route was selected).

### 0.2 Route cards and scores

- [x] `crowdFactor` is now always `null`; nothing may divide by or format it.
- [x] `score` is derived by the backend (fastest = 1.0, others = fastest / theirs) and is `null` until the final scores; render nothing (or `--`) while `null`.
- [x] `lengthM` is now 112–151 m: check `fmtDistance` still reads well ("151 m").
- [x] The crowd bar scaling (relative to the largest `peopleCount`) is unchanged, but counts are now small (10–26).

### 0.3 Scan copy (8 m, three headings)

- [x] `translations.js` (EN + AR): `launchSub` ("Climbs to {alt} m above the cart and runs a 360° scan of all routes"), `scanning` ("360° route scan"), `hovering` ("Rotating 360° · …"), `cam360` ("360° SCAN") and the `steps` entry "360° scan" describe the old 60 m / 360° scan. The drone now climbs to **8 m** above the cart and **turns to each route in turn** (A · West, B · North, C · East), holding each heading for a few seconds. Suggested: "Scans each route from above the cart" / "Facing {route} · counting people". `{alt}` comes from `mission.scanAltitudeM` (8).
- [x] `scan.progress.coveragePct` is now 0 / 33 / 67 / 100 by route; the scan sweep should show three discrete headings (`headingDeg` 315, 0, 45), not a continuous rotation.

### 0.4 Rescue at the incident (`arrived`)

- [x] `components/alerts/stages/ArrivedState.vue`: show the rescue progress from `mission.rescue.state`:
  - `in_progress`: "Medics treating the patient…" (with a spinner),
  - `patient_loaded`: "Patient on board — ready to return".
- [x] **Start return** is **disabled until `mission.rescue.state === 'patient_loaded'`**. A click before that returns `409 invalid_transition` ("Cannot return before the patient is loaded"); surface the message like the other command errors.
- [x] `useDashboardData.js`: `mission.state` messages with `from === to` (`reason: "patient_loaded"` / `"cart_at_hub"`) carry the updated mission. Already handled: same-state messages replace the local mission, as for `preselected`. No change needed.
- [x] `translations.js` (EN + AR): new strings for the two rescue states.

### 0.5 Return to the First Aid Center, then the landing

- [x] **"EMS Hub" → "First Aid Center"** everywhere (EN + AR): `hubShort`, `distHub` ("From EMS Hub"), `locHub`, `startReturn` ("Start return to EMS Hub"), `returningT`, `completedT` ("Back at EMS Hub"), `manualDrive`, the `steps` labels "Returning to hub" / "Back at hub", and the `CompletedState.vue` header comment. Arabic: "مركز الإسعافات الأولية" (replaces "مركز الإسعاف").
- [x] `stages/EscortState.vue` (`returning`): the destination is the First Aid Center. The return path is **not** just the route reversed: draw the return line from `mission.returnPolyline` (new in v1.4) and the travelled part from `escort.progress`.
- [x] When `mission.state` arrives with `reason: "cart_at_hub"` (still `returning`), show **"Drone landing on the cart…"** until `completed`. `completed` now means the drone has landed (`drone.dockedOnCart`).
- [x] `stages/CompletedState.vue`: "Cart at the First Aid Center · drone landed on the cart".

### 0.6 New map

The old walkway grid and Expo layout (`components/map/SiteMapBase.vue`, `data/siteMap.js` zones / landmarks / gates) do not match the world any more. Redraw the base map in the `1000 × 640` viewBox with the §2 projection (`x = 500 + 3.5·E`, `y = 526.5 − 3.5·N`):

- [x] Perimeter: x −95…95, y −32…150 m → SVG (167.5, 1.5)–(832.5, 638.5).
- [x] Avenues (8 m = 28 units wide): the entrance avenue (0, −32) → (0, 0), and the three route avenues = `mission.routes[].polyline` (or, before a mission, the same `routes.yaml` geometry: A (0,0)→(−36,36)→(−36,92)→(−16,112)→(0,112), B (0,0)→(0,98)→(0,112), C mirrored of A).
- [x] Start plaza: circle r 12 m at (0, 0) → (500, 526.5), r 42. Incident Plaza: r 14 m at (0, 112) → (500, 134.5), r 49.
- [x] First Aid Center: 14 × 11 m building centred at (17, −15), door facing west at (8.6, −15) = `site.hub` (530.1, 579); the returning cart stops at (0, −15) → (500, 579).
- [x] Replace the old zone / landmark / gate labels.
- [ ] **Still open:** the "100 m" scale bar in the map heading (`LiveMap.vue`) is the old fixed 36 px chip, not drawn to scale. At the §2 projection 100 m is 350 map units (35 % of the map width); draw it to scale (e.g. inside the SVG) or drop it.
- [x] The incident marker uses `incident.location.position` (always the world incident (500, 134.5)).
- [x] At boot the cart is at the start plaza (500, 526.5) facing north, with the drone docked on it.

### 0.7 Misc

- [x] Zones: `site.zones` is still `["A","B","C","D"]` from the old map and `incident.location.zone` is free text. Hide the zone if the new map has no zones.
- [x] `distanceFromHubM` is now ~127 m (First Aid Center door → incident).

## 1. Data layer: mock → backend

- [x] **`api/dashboardApi.js`:** replace the eight mock getters with `GET /api/snapshot`. Add command functions: `launch`, `dismiss`, `selectRoute(route)`, `setPayload({speaker, led})`, `abort(reason)`, `startReturn`, `close(outcome?)`, `createIncident(body)`. They should parse the error envelope `{error:{code,message}}` and surface the message.
- [x] **New `api/socket.js`:** connect to `/ws`, parse the `{type, data, ts}` envelope, ping every 5 s, and reconnect with backoff. On every (re)connect, replace all local state with the `snapshot` it receives.
- [x] **`composables/useDashboardData.js`:** load from the snapshot, then apply WS messages. These are `drone.telemetry`, `cart.telemetry`, `incident.*`, `mission.*`, `route.*`, `scan.progress`, `escort.progress`, `payload.state`, `camera.*`, `system.status` and `mission.history`.
- [x] **`composables/useMission.js`:** remove the client-side simulation, which is `useMissionLoop`, the `step()` timers and the local `sp` / `hold` / `p` counters. `mission` becomes the server `Mission`. `setStage` is replaced by the command functions. The speaker and LED toggles call `POST /payload` and render the confirmed `payload.state`, not an optimistic local flip.
- [x] **`vite.config.js`:** proxy `/api` and `/ws` (`ws: true`) to the backend.
- [x] **`data/mockData.js`:** keep it only for an offline/demo mode, or delete it.

## 2. Formatting (the backend now sends raw values)

- [x] Format these values in the frontend: `lengthM` → `'510 m'`, `estTimeS` / `elapsedS` / `responseTimeS` → `mm:ss` (`utils/geometry.js` `formatMmSs`), `distanceFromHubM` → `'0.7 km'`, `vsBestAlternativeS` (renamed from `vsOtherRouteS`) → signed `'+1:26'` / `'−1:26'`, and `reportedAt` → local `HH:mm`.
- [x] `flightTimeLeftS` → minutes (`DroneStatusCard`).
- [x] The incident title, type and location come from the `Incident` (`{en, ar}`). Remove the hard-coded `a1Title` / `a1Loc` / `a1Src` from `translations.js`, and map `source` to a label instead.
- [x] The history `zone` is now a letter (`"C"`), no longer an index. Update `RecentList.vue` (`t.zShort[r.z]`).
- [x] The history `outcome` values are `resolved | transferred | cancelled | aborted | error`. Add `outAborted` and `outError` labels.

## 3. Map: live positions

- [x] **`composables/useMissionView.js` `positions`:** use the server `drone.position.x/y` and `cart.position.x/y` instead of interpolating `ROUTE_POINTS` and the `ARRIVED` constants.
- [x] **Routes are a dynamic list (v1: A, B, C).** Render **one polyline per entry in `mission.routes`**, using `polyline[].x/y`. This replaces `ROUTE_POINTS` in `data/siteMap.js` and the hard-coded `A`/`B` list in `components/map/MissionOverlay.vue`. Draw the return leg along the selected route reversed.
- [x] `routeVariants` (`useMissionView.js`) becomes an object keyed by route id, built from the list. In it, the selected route is `sel`, the recommended route is `sel` before selection, and every other route is `alt`. `MapLabels.vue` renders one label per route, placed at the polyline midpoint. This replaces the mock `labelPos`.
- [x] **Route colours come from the route's index in the list,** not from its id. There is a palette of at least 3 tones: the existing `--amb` and `--acc`, plus a new third token in `styles/tokens.css` (light and dark).
- [x] `travelDash` uses `mission.escort.progress`. On the return leg, measure the travelled portion from the incident end of the route.
- [x] The scan sweep and heading come from `scan.progress.headingDeg` and `coveragePct`, replacing the values derived from `sp`.
- [x] **Drone home is the cart.** When `drone.dockedOnCart` is true, draw the drone on the cart marker instead of on `HUB_PAD`. `HUB_PAD` is renamed to mean the EMS hub, where the cart parks. Update `padHub` ("EMS Hub pad") in `DroneStatusCard` to "Cart dock" or similar.
- [x] The incident marker uses `incident.location.position`, replacing `INCIDENT_POINT`.

## 4. Mission stages: new states and buttons

`STAGES` grows from 5 stages to 9: `idle, alert, scan, escort, arrived, returning, completed, aborted, error`. The backend states are mapped to them as in API_CONTRACT §6.2. `taking_off`, `scanning` and `route_selected` all map to `scan`.

| Stage | Component | Change |
| --- | --- | --- |
| `alert` | `IncidentCard.vue` | Launch → `POST launch`. False alarm → `POST dismiss`. Show the `503 drone_unavailable` error message inline. |
| `scan` | `ScanState.vue` | Climb and rotation status come from `scan.phase`. **Render one route card per entry in `route.scores.routes` (3 cards in v1),** replacing the two hard-coded cards and `cntA`/`cntB` in `useMissionView.routeCards`. The crowd bar is scaled relative to the largest `peopleCount`, not by `/2`. `est` stays `--:--` until `estTimeS` is not null. **The "Best route" badge goes on `recommendedRoute`**, which is currently hard-coded to B in `useMissionView.routeCards`. The countdown is computed from `autoSelectAt` against server time. **New: one "Use route &lt;id&gt;" override button per route** → `POST route {route:id}`. They are available in `scanning` (pre-select, once the first `route.scores` has arrived) and `route_selected`. **Mark the pre-selected route** from `mission.preselectedRoute` during the scan. Hide the countdown when it is set, because the escort then starts on that route as soon as the recommendation arrives. |
| `scan` | `translations.js` | `autoSel` ("Route B selected automatically") must take the route name as a parameter |
| `escort` | `EscortState.vue` | Timer and ETA from `escort.elapsedS` / `etaS`, and drone-ahead distance from `escort.droneAheadOfCartM`. **Rename "Return to hub" → "Return to dock"** (`rth`), which calls `POST abort {reason:"return_to_dock"}`. Abort → `POST abort {reason:"operator_abort"}`. |
| `arrived` | `ArrivedState.vue` | **Replace both "Return SBQ-01 to hub" and "Close incident" with one "Start return" button** → `POST return`. The summary comes from `mission.result`, with `peopleWarned` as the final YOLO count on the selected route. |
| `returning` *(new)* | New `ReturningState.vue` | Reuse the escort layout (timer, camera, units, speaker/LED toggles) with a "Returning to EMS hub" title. Show `escort.leg = return` progress and the ETA. Provide Abort and Return to dock buttons. |
| `completed` *(new)* | New `CompletedState.vue` | Show a "Back at EMS hub" banner and the mission summary, plus two close buttons: **Resolved** → `POST close {outcome:"resolved"}` and **Transferred to hospital** → `POST close {outcome:"transferred"}`. |
| `aborted` *(new)* | New `AbortedState.vue` | Show a banner with `abortReason` and drone status ("Returning to cart dock…" until `dockedOnCart`). Add a note that the operator should drive the cart back to the hub manually. Provide a **Close** button → `POST close`. |
| `error` *(new)* | New `ErrorState.vue` | Show a red banner with `mission.error.code` and `message`, plus live drone status. Provide an **Acknowledge & close** button → `POST close`. |

- [x] **`AlertsPanel.vue`:** render the four new stage components. Keep `IncidentCard` visible in every non-idle stage.
- [x] **`translations.js` (EN + AR):** add `states` and `stageNames` entries for the 4 new stages. Add `steps` entries `Returning` and `Completed`. Add strings for `startReturn`, `returnToDock`, `resolved`, `transferred`, `ackClose`, the aborted and error banners, and a parameterised `useRoute` ("Use {route}"). Replace `routeA` and `routeB` with a single `routeName` template ("Route {id}" / "المسار {id}").
- [ ] **Mission timeline:** if the `steps` stepper is rendered, drive it from `mission.timeline`. **Not applicable yet**: no component renders the `steps` stepper; the strings are updated.

## 5. Camera

- [x] **`CameraFeed.vue`: change the view to 4:3** (`aspect-ratio: 4 / 3`, from `16 / 10`) to match the 1280×960 camera. Show `<img :src="camera.streamUrl">` (raw MJPEG, no boxes burnt in) filling the view, with `object-fit: fill` so that the percent coordinates line up exactly. Hide it when `camera.available` is false.
- [x] **Draw the boxes in the frontend** from `camera.detections.boxes` as an absolutely positioned overlay: `left: x%`, `top: y%`, `width: w%`, `height: h%`. `x`/`y` are the top-left corner. This replaces today's fixed `5% × 13%` box size and `x`/`y`-only placement. Show the real `conf`, replacing the pseudo-confidence `conf(i)`.
- [x] Boxes have **no route**, so all of them use one neutral tone. Remove the per-route tinting and the A/B split line.
- [x] The per-route counts overlay is rendered from `camera.detections.counts` (an object keyed by route id, possibly `null`). There is one chip per route, replacing the hard-coded `A · cntA` / `B · cntB`.
- [x] Remove the mock `cameraDetections` and the `forward` box drift animation.
- [x] The camera mode label comes from `camera.info.mode` (`scan_360` / `forward`).

## 6. Header and misc

- [x] **Status chips** come from `system.status`: ROS 2 OK/fail, and the detection rate (`system.jetson.fps`, shown as the **YOLO** chip: in the simulation YOLO runs on the PC, not a Jetson). The **WS latency** chip comes from the ping/pong round trip, replacing `baseMs + jitter` in `useClock`.
- [x] The alert count is `openIncidents.active + openIncidents.queued`. There is **no queued-incident list in v1**.
- [x] `site.visitors` and the scale bar come from `Site`. `visitors` is `null` unless `SITE_VISITORS` is set; the map shows "—" then.
- [x] Show a disconnected or "reconnecting…" state when the WS drops, or when `system.ros.ok` is false.
- [x] **`StageNav.vue`:** the demo stage jumps have no backend equivalent. Hide them when connected to a backend, or keep them only in the offline mock mode. For backend demos, add an optional "Reset sim" button → `POST /api/dev/sim/reset`. It returns 404 unless `DEV_MODE=true`, so show it only when the call succeeds.
- [ ] **Manual incident entry:** a small form (severity, type, zone, location text, map click for x/y) → `POST /incidents {source:"operator"}`. It is optional for the first frontend pass, but the endpoint exists. **Not done yet**: incidents come from `POST /api/incidents` (curl, dispatch).
