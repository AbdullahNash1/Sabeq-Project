// Pure geometry / formatting helpers for the mission map.

export const pathLength = (pts) => {
  let len = 0
  for (let i = 1; i < pts.length; i++) {
    len += Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1])
  }
  return len
}

// Point at fraction t (0..1) along a polyline
export const pointAt = (pts, t) => {
  let d = t * pathLength(pts)
  for (let i = 1; i < pts.length; i++) {
    const a = pts[i - 1]
    const b = pts[i]
    const sl = Math.hypot(b[0] - a[0], b[1] - a[1])
    if (d <= sl) {
      const k = sl ? d / sl : 0
      return [a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k]
    }
    d -= sl
  }
  return pts[pts.length - 1]
}

export const lerp = (a, b, k) => [a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k]

// Point at compass angle (deg, 0 = north) and radius r from origin
export const polar = (origin, deg, r) => [
  origin[0] + r * Math.sin((deg * Math.PI) / 180),
  origin[1] - r * Math.cos((deg * Math.PI) / 180),
]

// SVG path for a pie wedge from a0 to a1 degrees
export const wedgePath = (origin, a0, a1, r = 900) => {
  const n = Math.max(2, Math.ceil((a1 - a0) / 5))
  let d = 'M' + origin.join(',')
  for (let i = 0; i <= n; i++) {
    const q = polar(origin, a0 + ((a1 - a0) * i) / n, r)
    d += ' L' + q[0].toFixed(1) + ',' + q[1].toFixed(1)
  }
  return d + ' Z'
}

export const formatMmSs = (s) => {
  s = Math.max(0, Math.round(s))
  return String(Math.floor(s / 60)).padStart(2, '0') + ':' + String(s % 60).padStart(2, '0')
}

export const toTranslate = (p) => `translate(${p[0].toFixed(1)},${p[1].toFixed(1)})`
