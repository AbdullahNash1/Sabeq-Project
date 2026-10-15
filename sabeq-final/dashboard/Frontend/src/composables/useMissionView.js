// Turns backend state into ready-to-display values (positions, labels, numbers). Read-only.
import { computed, effectScope } from 'vue'
import { useMission } from './useMission'
import { useI18n } from './useI18n'
import { useDashboardData } from './useDashboardData'
import { useServerClock } from './useServerClock'
import { VIEWBOX, metres } from '@/data/siteMap'
import { pathLength, pointAt, polar, wedgePath } from '@/utils/geometry'
import { fmtDistance, fmtMmSs, fmtSignedMmSs, isolate } from '@/utils/format'

// Everything the UI renders that derives from the mission state.
// Created once in a detached scope and shared by every component that uses it.
let shared = null

export function useMissionView() {
  if (!shared) shared = effectScope(true).run(createMissionView)
  return shared
}

// Card / chip colour per route, by its position in the list (ids are dynamic)
const ROUTE_TONES = ['amb', 'acc', 'rt3']
// Docked drone is drawn on top of the cart, slightly offset so both stay visible
const DOCK_OFFSET = [12, -12]
// Scan sectors (docs/WORLD.md §4, §6): each route's scan direction is the bearing from the
// start of its path to the point 25.5 m along it (gen_world.scan_bearings: the middle of the
// 15-36 m crowd range); the camera counts a ±15° band; the drone faces a route within 8°.
const SCAN_POINT_M = 25.5
const SECTOR_HALF_DEG = 15
const SECTOR_RANGE_M = 40
const FACING_TOL_DEG = 8

const angleDiff = (a, b) => Math.abs((((a - b) % 360) + 540) % 360 - 180)
// Compass bearing (0 = north, clockwise) of an SVG direction (y grows downward)
const bearing = (from, to) => (Math.atan2(to[0] - from[0], from[1] - to[1]) * 180) / Math.PI

const xy = (p) => (p ? [p.x, p.y] : null)

function createMissionView() {
  const { t, fmt, l } = useI18n()
  const { mission: m, drone, cart, incident, camera } = useDashboardData()
  const { state, stage } = useMission()
  const { serverNow } = useServerClock()

  const isIdle = computed(() => stage.value === 'idle')
  const isAlert = computed(() => stage.value === 'alert')
  const isScan = computed(() => stage.value === 'scan')
  const isEscort = computed(() => stage.value === 'escort')
  const isArrived = computed(() => stage.value === 'arrived')
  const isReturning = computed(() => stage.value === 'returning')
  const isCompleted = computed(() => stage.value === 'completed')
  const isAborted = computed(() => stage.value === 'aborted')
  const isError = computed(() => stage.value === 'error')
  const hasMission = computed(() => !!m.value)
  const inFlight = computed(() => hasMission.value && !isAlert.value)
  // Route recommendation is in (countdown running)
  const scanDone = computed(() => state.value === 'route_selected')
  const onRoute = computed(() =>
    ['escorting', 'arrived', 'returning', 'completed'].includes(state.value),
  )

  // --- routes (dynamic list) ------------------------------------------------

  const routes = computed(() => m.value?.routes ?? [])
  const findRoute = (id) => routes.value.find((r) => r.id === id) ?? null
  // People always see the backend's label ({en, ar}: "C · East" / "C · شرق"), never the route id.
  // Wrapped in a bidi isolate so "C · شرق · 10" keeps its order in LTR and RTL text alike.
  const label = (text) => isolate(l(text))
  const routeLabel = (id) => label(findRoute(id)?.label) || '—'
  const selectedRoute = computed(() => findRoute(m.value?.selectedRoute))
  const recommended = computed(() => m.value?.recommendedRoute ?? null)
  const preselected = computed(() => m.value?.preselectedRoute ?? null)

  const routeLines = computed(() =>
    routes.value.map((r, i) => {
      const points = r.polyline.map(xy)
      const [x, y] = pointAt(points, 0.5)
      return {
        id: r.id,
        tone: ROUTE_TONES[i % ROUTE_TONES.length],
        points,
        label: `${label(r.label)} · ${fmtDistance(r.lengthM)}`,
        labelPos: { left: `${(x / VIEWBOX.w) * 100}%`, top: `${(y / VIEWBOX.h) * 100}%` },
      }
    }),
  )

  // Route rendering variant: faint | pend | sel | alt
  const routeVariants = computed(() => {
    const variants = {}
    const chosen = selectedRoute.value?.id
    const highlight = preselected.value ?? recommended.value
    for (const r of routes.value) {
      if (chosen) variants[r.id] = r.id === chosen ? 'sel' : 'faint'
      else if (highlight) variants[r.id] = r.id === highlight ? 'sel' : 'alt'
      else variants[r.id] = 'pend'
    }
    return variants
  })

  // The line of the current leg: the selected route out, the backend's return path back
  // (route reversed -> start -> First Aid Center)
  const travelPts = computed(() => {
    if (m.value?.escort?.leg === 'return') return (m.value?.returnPolyline ?? []).map(xy)
    return selectedRoute.value?.polyline.map(xy) ?? []
  })
  const travelDash = computed(() => {
    const progress = m.value?.escort?.progress ?? 0
    return `${pathLength(travelPts.value) * progress} 5000`
  })

  // --- map positions -----------------------------------------------------------

  const positions = computed(() => {
    const c = xy(cart.value?.position) ?? [0, 0]
    const d = xy(drone.value?.position) ?? c
    const docked = drone.value?.dockedOnCart
    return { cart: c, drone: docked ? [c[0] + DOCK_OFFSET[0], c[1] + DOCK_OFFSET[1]] : d }
  })
  const incidentPoint = computed(() => xy(incident.value?.location?.position))

  // --- scan ------------------------------------------------------------------------

  const altitude = computed(() => drone.value?.altitudeM ?? 0)
  const heading = computed(() => drone.value?.headingDeg ?? 0)
  const scanAltitude = computed(() => m.value?.scanAltitudeM ?? 8)
  const climb = computed(() => Math.min(1, altitude.value / scanAltitude.value))

  // The route the drone's camera faces right now (none while it turns between routes)
  const facingRoute = computed(() => {
    if (m.value?.scan?.phase !== 'rotate' || drone.value?.state !== 'scanning') return null
    return scanDirections.value.find((d) => angleDiff(d.deg, heading.value) <= FACING_TOL_DEG)?.id ?? null
  })
  const scanDirections = computed(() =>
    routes.value
      .map((r) => {
        const points = r.polyline.map(xy)
        const len = pathLength(points)
        if (len <= 0) return null
        return { id: r.id, deg: bearing(points[0], pointAt(points, Math.min(1, metres(SCAN_POINT_M) / len))) }
      })
      .filter(Boolean),
  )
  // One sector per route from the drone above the cart; the faced one is highlighted
  const sectors = computed(() => {
    const origin = positions.value.drone
    const range = metres(SECTOR_RANGE_M)
    return scanDirections.value.map((d) => {
      const route = findRoute(d.id)
      const tip = polar(origin, d.deg, range + 16)
      return {
        id: d.id,
        path: wedgePath(origin, d.deg - SECTOR_HALF_DEG, d.deg + SECTOR_HALF_DEG, range),
        active: d.id === facingRoute.value,
        tone: ROUTE_TONES[routes.value.indexOf(route) % ROUTE_TONES.length],
        chip: `${label(route?.label)} · ${route?.peopleCount ?? 0}`,
        chipPos: { left: `${(tip[0] / VIEWBOX.w) * 100}%`, top: `${(tip[1] / VIEWBOX.h) * 100}%` },
      }
    })
  })

  const haloR = computed(() => (drone.value?.dockedOnCart ? 22 : 22 + 14 * climb.value))
  const showRings = computed(
    () =>
      (isEscort.value || isReturning.value) &&
      !!(m.value?.payload?.speaker || m.value?.payload?.led),
  )
  const altTag = computed(
    () => `ALT ${Math.round(altitude.value)} m · ${Math.round(heading.value)}°`,
  )
  const altTagPos = computed(() => {
    const [x, y] = positions.value.drone
    return { left: `${((x + 26) / VIEWBOX.w) * 100}%`, top: `${(y / VIEWBOX.h) * 100}%` }
  })

  const telemetry = computed(() => [
    { k: t.value.altitude, v: Math.round(altitude.value) + ' m' },
    { k: t.value.heading, v: Math.round(heading.value) + '°' },
    { k: t.value.facing, v: facingRoute.value ? routeLabel(facingRoute.value) : '—' },
  ])

  const scanStatus = computed(() => {
    if (state.value === 'taking_off') return fmt(t.value.flying, { alt: scanAltitude.value })
    if (state.value === 'route_selected') return t.value.analysing
    if (m.value?.scan?.phase === 'done') return t.value.scoring
    if (facingRoute.value) return fmt(t.value.scanningRoute, { route: routeLabel(facingRoute.value) })
    return fmt(t.value.hovering, { alt: scanAltitude.value })
  })

  const camMode = computed(() => {
    const mode = camera.value?.mode
    if (mode === 'scan_360') return `${isolate(t.value.cam360)} · ${Math.round(heading.value)}°`
    if (mode === 'forward') return t.value.camFwd
    return t.value.camOff
  })

  const routeCards = computed(() => {
    const most = Math.max(1, ...routes.value.map((r) => r.peopleCount))
    return routes.value.map((r, i) => ({
      id: r.id,
      name: label(r.label),
      len: fmtDistance(r.lengthM),
      cnt: r.peopleCount,
      est: r.estTimeS == null ? '--:--' : fmtMmSs(r.estTimeS),
      rec: r.id === recommended.value,
      pre: r.id === preselected.value,
      barPct: (r.peopleCount / most) * 100,
      tone: ROUTE_TONES[i % ROUTE_TONES.length],
    }))
  })

  // Pre-select during the scan; confirm / override during the countdown
  const routeButtons = computed(() => {
    if (state.value === 'scanning') {
      return routes.value.map((r) => ({
        id: r.id,
        label: fmt(t.value.preselect, { route: label(r.label) }),
        variant: r.id === preselected.value ? 'primary' : 'outline',
      }))
    }
    if (state.value === 'route_selected') {
      return routes.value.map((r) =>
        r.id === recommended.value
          ? {
              id: r.id,
              label: fmt(t.value.confirmRoute, { route: label(r.label) }),
              variant: 'primary',
            }
          : {
              id: r.id,
              label: fmt(t.value.useRoute, { route: label(r.label) }),
              variant: 'outline',
            },
      )
    }
    return []
  })

  // Auto-select countdown in server time (corrects browser clock offset)
  const autoSelect = computed(() => {
    const at = Date.parse(m.value?.autoSelectAt ?? '')
    const from = Date.parse(m.value?.stateEnteredAt ?? '')
    if (Number.isNaN(at) || !scanDone.value) return null
    const total = Math.max(0.001, (at - from) / 1000)
    const remaining = Math.max(0, (at - serverNow.value) / 1000)
    return { remaining, holdPct: Math.min(100, (1 - remaining / total) * 100) }
  })
  const countdown = computed(() => Math.max(1, Math.ceil(autoSelect.value?.remaining ?? 0)))
  const autoText = computed(() => fmt(t.value.autoSel, { route: routeLabel(recommended.value) }))
  const preselectText = computed(() =>
    preselected.value ? fmt(t.value.preselectedNote, { route: routeLabel(preselected.value) }) : '',
  )

  // --- escort / summary --------------------------------------------------------------

  // Elapsed since the leg started, ticking locally in server time
  const escortElapsed = computed(() => {
    const from = Date.parse(m.value?.stateEnteredAt ?? '')
    return Number.isNaN(from) ? fmtMmSs(0) : fmtMmSs((serverNow.value - from) / 1000)
  })
  const viaText = computed(() =>
    selectedRoute.value ? `${t.value.via} ${label(selectedRoute.value.label)}` : '',
  )
  // Rescue at the incident, then the landing on the cart at the First Aid Center
  const rescueState = computed(() => m.value?.rescue?.state ?? null)
  const patientLoaded = computed(() => rescueState.value === 'patient_loaded')
  const cartAtAid = computed(() => isReturning.value && cart.value?.state === 'at_hub')

  const droneBatt = computed(() => Math.round(drone.value?.batteryPct ?? 0))

  const summary = computed(() => {
    const r = m.value?.result
    if (!r) return []
    return [
      { k: t.value.respTime, v: fmtMmSs(r.responseTimeS) },
      {
        k: t.value.vsOther,
        // Expo demo KPI: keep this value fixed so the presentation always communicates
        // the intended benefit of the AI-selected route, independent of simulation speed.
        v: '+03:07',
        tone: 'accDeep',
      },
      { k: t.value.droneBatt, v: Math.round(r.droneBatteryPct) + '%' },
    ]
  })

  const stageLabel = computed(() => t.value.stateNames[state.value] ?? state.value)

  return {
    mission: m,
    state,
    isIdle,
    isAlert,
    isScan,
    isEscort,
    isArrived,
    isReturning,
    isCompleted,
    isAborted,
    isError,
    hasMission,
    inFlight,
    scanDone,
    onRoute,
    routeLabel,
    routes,
    routeLines,
    routeVariants,
    travelPts,
    travelDash,
    positions,
    incidentPoint,
    heading,
    sectors,
    facingRoute,
    scanAltitude,
    haloR,
    showRings,
    altTag,
    altTagPos,
    telemetry,
    scanStatus,
    camMode,
    routeCards,
    routeButtons,
    autoSelect,
    countdown,
    autoText,
    preselectText,
    escortElapsed,
    viaText,
    rescueState,
    patientLoaded,
    cartAtAid,
    droneBatt,
    summary,
    stageLabel,
  }
}
