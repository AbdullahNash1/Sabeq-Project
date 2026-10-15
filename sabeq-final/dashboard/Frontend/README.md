# Sabeq Dashboard

Operations dashboard for Sabeq, Expo 2030's emergency route assistant. A drone scans the candidate routes, picks the fastest one given the crowd, and escorts the EMS cart to the patient and back to the First Aid Center. The map is the Gazebo simulation world (`../docs/WORLD.md`).

Built with Vue 3 (Composition API, `<script setup>`) and Vite 8. Requires **Node.js 22 LTS** (22.18 or newer; `nvm use` reads `.nvmrc`). On the Linux test machine, `linux/run/6_frontend.sh` does all of this.

The dashboard is a live client of the FastAPI backend in `../Backend` (see `../Backend/API_CONTRACT.md`). Start the backend first. With all sources on mock it runs a complete simulated mission:

```sh
# terminal 1: backend on :8000 (see ../Backend/DEVELOPMENT.md)
cd ../Backend && DEV_MODE=true MOCK_SPEED=5 .venv/bin/uvicorn app.main:app --reload

# terminal 2: dashboard on :5173; /api and /ws are proxied to the backend
nvm use        # Node 22 LTS
npm install
VITE_DEV_MODE=true npm run dev   # VITE_DEV_MODE shows the "Reset sim" button
npm run build                    # production build
```

Point the proxy at another backend with `BACKEND_URL=http://host:port npm run dev`.

---

## The screen

The page has four areas, and each one has its own folder in `src/components/`:

```
┌──────────────────────────────────────────────────────────────┐
│  header/          logo · status chips · clock · EN/AR · theme│
├──────┬──────────────────────────────────────┬────────────────┤
│stage-│  map/                                │  alerts/       │
│nav/  │                                      │                │
│      │  live site map with drone, cart,     │  incident card │
│ Idle │  routes and scan sweep               │  + the panel   │
│ Alert│                                      │  for the       │
│ Scan │                                      │  current stage │
│ ...  │                                      │                │
└──────┴──────────────────────────────────────┴────────────────┘
```

`src/views/DashboardView.vue` is the page that places these four areas in a grid.

---

## Folder guide

```
src/
├── main.js                 App entry: loads the CSS and starts Vue
├── App.vue                 Root component (only shows the current page)
├── router/                 URL → page   ( "/" → DashboardView )
├── views/
│   └── DashboardView.vue   ★ The page. Start reading here.
│
├── components/             Everything you SEE
│   ├── header/             Top bar
│   ├── stage-nav/          Left column with dev controls ("Reset sim"; only with VITE_DEV_MODE=true)
│   ├── map/                The live map
│   │   ├── LiveMap.vue         the map card (puts the pieces below together)
│   │   ├── SiteMapBase.vue     static drawing of the site, from data/world.json
│   │   ├── MissionOverlay.vue  moving parts: drone, cart, routes, scan sweep
│   │   ├── MapLabels.vue       text on the map
│   │   ├── MapLegend.vue
│   │   └── ZoomControls.vue
│   ├── alerts/             Right-hand panel
│   │   ├── AlertsPanel.vue     the panel (picks which stage to show)
│   │   ├── stages/             one file per mission stage
│   │   │   ├── IdleState.vue
│   │   │   ├── ScanState.vue
│   │   │   ├── EscortState.vue     (escort and return leg)
│   │   │   ├── ArrivedState.vue
│   │   │   ├── CompletedState.vue
│   │   │   ├── AbortedState.vue
│   │   │   └── ErrorState.vue
│   │   └── …                   cards used by those stages
│   └── ui/                 Small reusable pieces with no business logic
│                           (buttons, tiles, progress bar, toggle, segmented control)
│
├── composables/            Everything that CHANGES (reactive state and logic)
│   ├── useDashboardData.js ★ backend state, filled only from the WebSocket
│   ├── useMission.js       ★ backend mission state → UI stage, plus the REST commands
│   ├── useMissionView.js   turns mission state into values to display
│   ├── useServerClock.js   "now" in server time (countdowns, timers)
│   ├── useNotices.js       error toasts for the operator
│   ├── useI18n.js          language + translations + RTL
│   ├── useTheme.js         light / dark
│   ├── useClock.js         live clock
│   └── useMapZoom.js       map zoom
│
├── api/
│   ├── socket.js           ★ the single WebSocket client (snapshot, updates, ping, reconnect)
│   ├── dashboardApi.js     REST commands (launch, route, abort, return, close, …)
│   └── http.js             fetch wrapper; errors arrive as the contract's error envelope
├── data/                   Static data
│   ├── world.json          GENERATED: 2D layout of the Gazebo world (see "The map" below)
│   ├── siteMap.js          world.json projected into the 1000×640 viewBox (API_CONTRACT §2) + place labels
│   └── translations.js     all English and Arabic text
├── utils/
│   ├── geometry.js         plain maths helpers (point on a path, mm:ss format…)
│   └── format.js           raw backend values → text (m, mm:ss, km/h, %, {en, ar})
└── styles/
    ├── tokens.css          ★ all colours, fonts, spacing and radii (light + dark)
    ├── base.css            page-wide defaults
    └── animations.css      pulse / blink / slide-in keyframes
```

**Rule of thumb:** `components/` holds what you see, `composables/` holds what changes, `data/` and `api/` hold where the data comes from, and `styles/` holds how things look.

---

## How data flows

```
backend ──WebSocket /ws──▶ api/socket.js ──▶ useDashboardData.js ──▶ useMission / useMissionView ──▶ components
   ▲        snapshot on connect,              (shared reactive state)    (stage, positions, labels)
   │        then live updates
   └──────── REST /api (commands only) ◀── useMission() actions ◀── buttons
```

- **State comes only from the WebSocket.** On every (re)connect the `snapshot` replaces all local state, and the other messages patch it. REST responses are never applied to state.
- **Clicking Launch drone** calls `useMission().launch()`, which sends `POST /api/missions/{id}/launch`. The backend then broadcasts `mission.state`, the stage computed by `useMission` changes, and the panels and map follow. While a command is in flight its buttons are disabled. Failures appear as a toast.
- **There is no client-side simulation.** Positions, scan progress, counts and timers all come from the backend. Countdowns use the server clock, corrected by the ping/pong offset.

---

## Common tasks

| I want to…                         | Edit this                                   |
| ---------------------------------- | ------------------------------------------- |
| Change a colour / font / spacing   | `src/styles/tokens.css`                     |
| Change any text (EN or AR)         | `src/data/translations.js`                  |
| Handle a new backend WS message    | `src/composables/useDashboardData.js` (`handlers`) |
| Add a REST command                 | `src/api/dashboardApi.js` + `src/composables/useMission.js` |
| Change mock mission timing         | backend: `MOCK_SPEED`, `MOCK_BEST_ROUTE`, `MOCK_LOW_BATTERY` |
| Change what a stage panel shows    | `src/components/alerts/stages/…`            |
| Change the map drawing             | `src/components/map/SiteMapBase.vue`        |
| Update the map after the world changed | `python3 tools/export_world.py` (see below) |
| Add a new reusable button/tile     | `src/components/ui/`                        |

---

## The map

The site map is the Gazebo world itself, not a drawing. `src/data/world.json` holds its 2D footprints in world ENU metres (site wall, avenues with sidewalks, the start plaza, the Incident Plaza, the First Aid Center, gardens, fountains, shade canopies and kiosks). It is **generated** from the teammates' `docs/team/gen_world.py` by `tools/export_world.py`, which only reads that file. Never edit `world.json` by hand.

When `gen_world.py` changes, run from the repository root:

```sh
python3 tools/export_world.py          # rewrites Frontend/src/data/world.json
python3 tools/export_world.py --check  # exit 1 if world.json is out of date
```

then rebuild the frontend. `tools/test_export_world.py` fails when `world.json` is stale.

`src/data/siteMap.js` projects the layout with the backend's projection (API_CONTRACT §2): `x = 500 + 3.5·E`, `y = 526.5 − 3.5·N`, north up, 100 m = 350 units. Everything that moves or belongs to a mission (routes, return path, incident, drone, cart) comes from the backend, already projected. Route names on screen are always the backend's `label` ("C · East"), never the route id.
