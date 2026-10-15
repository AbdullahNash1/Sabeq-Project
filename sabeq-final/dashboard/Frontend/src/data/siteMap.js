// Static site map: the Gazebo world's 2D layout (world.json, exported from
// docs/team/gen_world.py by tools/export_world.py), projected into the SVG viewBox
// with the backend's projection (API_CONTRACT §2). Mission geometry (routes, incident,
// drone, cart) comes from the backend only.
import world from './world.json'

export const VIEWBOX = { w: 1000, h: 640 }

// API_CONTRACT §2: x = 500 + 3.5·E, y = 526.5 − 3.5·N (north up, no rotation)
export const PROJECTION = { x0: 500, y0: 526.5, scale: 3.5 }
export const project = ([e, n]) => [
  PROJECTION.x0 + PROJECTION.scale * e,
  PROJECTION.y0 - PROJECTION.scale * n,
]
export const metres = (m) => m * PROJECTION.scale

const r1 = (v) => Math.round(v * 10) / 10
const pts = (list) => list.map((p) => project(p).map(r1).join(',')).join(' ')
// ENU rectangle [x0, y0, x1, y1] -> SVG rect attributes (y flips)
const rect = ([x0, y0, x1, y1]) => {
  const [ax, ay] = project([x0, y1])
  return { x: r1(ax), y: r1(ay), width: r1(metres(x1 - x0)), height: r1(metres(y1 - y0)) }
}
const boxAround = ([cx, cy], [sx, sy]) => rect([cx - sx / 2, cy - sy / 2, cx + sx / 2, cy + sy / 2])
const circle = (c, r) => {
  const [cx, cy] = project(c)
  return { cx: r1(cx), cy: r1(cy), r: r1(metres(r)) }
}
// Footprint rotated by an ENU yaw (counter-clockwise from east)
const rotatedBox = ([cx, cy], [sx, sy], yaw) => {
  const c = Math.cos(yaw)
  const s = Math.sin(yaw)
  const corners = [
    [-sx / 2, -sy / 2],
    [sx / 2, -sy / 2],
    [sx / 2, sy / 2],
    [-sx / 2, sy / 2],
  ].map(([lx, ly]) => [cx + c * lx - s * ly, cy + s * lx + c * ly])
  return pts(corners)
}

const { site, firstAid } = world
const plazaOf = (id) => world.plazas.find((p) => p.id === id)

export const siteMap = {
  site: rect([site.xmin, site.ymin, site.xmax, site.ymax]),
  roadWidth: metres(world.roadWidth),
  walkwayWidth: metres(world.roadWidth + 2 * world.walkWidth),
  avenues: world.avenues.map(pts),
  corners: world.corners.map((p) => circle(p, world.roadWidth / 2 + world.walkWidth)),
  plazas: world.plazas.map((p) => ({ id: p.id, ...circle(p.center, p.radius) })),
  gardens: world.gardens.map(pts),
  plots: world.plots.map((p) =>
    p.rect ? { kind: 'rect', ...rect(p.rect) } : { kind: 'circle', ...circle(p.circle, p.circle[2]) },
  ),
  canopies: world.canopies.map((c) => boxAround(c.center, c.size)),
  fountains: world.fountains.map((f) => ({ rim: circle(f.center, f.radius), pool: circle(f.center, f.pool) })),
  kiosks: world.kiosks.map((k) => rotatedBox(k.center, k.size, k.yawRad)),
  firstAid: {
    building: boxAround(firstAid.center, firstAid.size),
    center: project(firstAid.center).map(r1),
    door: project(firstAid.door).map(r1),
    cartStop: project(firstAid.cartStop).map(r1),
  },
}

// Overlay text labels (HTML over the SVG), in % of the map box
const pct = ([x, y], dx = 0, dy = 0) => ({
  left: `${((x + dx) / VIEWBOX.w) * 100}%`,
  top: `${((y + dy) / VIEWBOX.h) * 100}%`,
})
const start = plazaOf('start')
const incident = plazaOf('incident')
export const placeLabels = [
  // First Aid Center: to the right of the building (the start plaza is on its left)
  {
    key: 'firstAidCenter',
    anchor: 'start',
    tone: 'aid',
    style: pct(project([firstAid.center[0] + firstAid.size[0] / 2, firstAid.center[1]]), 8, 0),
  },
  // Start: to the left of the start plaza (the cart and drone sit in it)
  { key: 'startLabel', anchor: 'end', tone: 'site', style: pct(project(start.center), -metres(start.radius) - 8, 0) },
  // Incident Plaza: above the plaza (the incident tag sits on the marker inside it)
  { key: 'incidentPlaza', anchor: 'center', tone: 'site', style: pct(project(incident.center), 0, -metres(incident.radius) - 14) },
]
