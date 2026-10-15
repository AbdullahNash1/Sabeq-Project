// Backend state, shared app-wide. Filled ONLY from the WebSocket (api/socket.js):
// the `snapshot` message replaces everything, every other §5.2 message patches it.
import { computed, reactive } from 'vue'

const HISTORY_LIMIT = 10

const state = reactive({
  ready: false, // first snapshot received
  connection: 'connecting', // connecting | open | reconnecting
  site: null,
  system: null,
  drone: null,
  cart: null,
  camera: null,
  mission: null, // active mission (null when idle)
  incident: null, // active incident
  openIncidents: { active: 0, queued: 0 },
  recentMissions: [],
  detections: null, // latest camera.detections data
  detectionsAt: 0, // local ms when it arrived
  latencyMs: null, // ping/pong round trip
  clockOffsetMs: 0, // server clock - browser clock
})

export function applySnapshot(s, ts) {
  Object.assign(state, {
    site: s.site,
    system: s.system,
    drone: s.drones[0] ?? null,
    cart: s.carts[0] ?? null,
    camera: s.camera,
    mission: s.activeMission,
    incident: s.activeIncident,
    openIncidents: s.openIncidents,
    recentMissions: s.recentMissions,
    detections: null,
    detectionsAt: 0,
    ready: true,
  })
  // First clock estimate; refined by every pong.
  const server = Date.parse(s.serverTime || ts)
  if (!Number.isNaN(server)) state.clockOffsetMs = server - Date.now()
}

// Fields of the active mission, only if the message is about it
const forMission = (data, patch) => {
  if (state.mission && data.missionId === state.mission.id) patch(state.mission)
}

const handlers = {
  'system.status': (d) => (state.system = d),
  'incident.created': onIncident,
  'incident.updated': onIncident,
  'mission.state': (d) => {
    if (d.to === 'idle') {
      if (state.mission?.id === d.missionId) state.mission = null
    } else {
      // Also for from == to (e.g. reason "preselected"): a refresh, not a transition.
      state.mission = d.mission
    }
  },
  'drone.telemetry': (d) => {
    // eslint-disable-next-line no-unused-vars
    const { droneId, ...fields } = d
    if (state.drone) Object.assign(state.drone, fields)
  },
  'cart.telemetry': (d) => {
    // eslint-disable-next-line no-unused-vars
    const { cartId, ...fields } = d
    if (state.cart) Object.assign(state.cart, fields)
  },
  'scan.progress': (d) =>
    forMission(d, (m) => {
      m.scan = { phase: d.phase, progress: d.progress, coveragePct: d.coveragePct }
    }),
  'route.scores': (d) => forMission(d, (m) => (m.routes = d.routes)),
  'route.recommendation': (d) =>
    forMission(d, (m) => {
      m.routes = d.routes
      m.recommendedRoute = d.recommended
      m.autoSelectAt = d.autoSelectAt
    }),
  'route.selected': (d) =>
    forMission(d, (m) => {
      m.selectedRoute = d.route
      m.selectionSource = d.source
    }),
  'escort.progress': (d) =>
    forMission(d, (m) => {
      Object.assign(m.escort, {
        leg: d.leg,
        progress: d.progress,
        elapsedS: d.elapsedS,
        etaS: d.etaS,
        droneAheadOfCartM: d.droneAheadOfCartM,
      })
    }),
  'payload.state': (d) => forMission(d, (m) => (m.payload = { speaker: d.speaker, led: d.led })),
  'camera.detections': (d) => {
    state.detections = d
    state.detectionsAt = Date.now()
  },
  'camera.info': (d) => (state.camera = d),
  'mission.history': (d) => {
    state.recentMissions = [
      d.item,
      ...state.recentMissions.filter((m) => m.id !== d.item.id),
    ].slice(0, HISTORY_LIMIT)
  },
}

function onIncident(d) {
  state.openIncidents = d.openIncidents
  const inc = d.incident
  if (inc.status === 'active') state.incident = inc
  else if (state.incident?.id === inc.id) state.incident = null
}

// Returns false for types this store doesn't handle (error, pong are handled elsewhere).
export function applyMessage(type, data) {
  const handler = handlers[type]
  if (!handler) return false
  handler(data)
  return true
}

export function setConnection(value) {
  state.connection = value
}

export function setClock(latencyMs, clockOffsetMs) {
  state.latencyMs = latencyMs
  state.clockOffsetMs = clockOffsetMs
}

const site = computed(() => state.site)
const system = computed(() => state.system)
const drone = computed(() => state.drone)
const cart = computed(() => state.cart)
const camera = computed(() => state.camera)
const mission = computed(() => state.mission)
const incident = computed(() => state.incident)
const openIncidents = computed(() => state.openIncidents)
const recentMissions = computed(() => state.recentMissions)
const detections = computed(() => state.detections)
const ready = computed(() => state.ready)
const connection = computed(() => state.connection)
const latencyMs = computed(() => state.latencyMs)

export function useDashboardData() {
  return {
    state,
    site,
    system,
    drone,
    cart,
    camera,
    mission,
    incident,
    openIncidents,
    recentMissions,
    detections,
    ready,
    connection,
    latencyMs,
  }
}
