// Formatting for raw backend values (metres, seconds, km/h, %, ISO times, {en, ar}).
import { formatMmSs } from './geometry'

export const DASH = '—'

const isNum = (v) => typeof v === 'number' && Number.isFinite(v)

// 0 or null means "unknown" for distances (projection not configured)
export const fmtDistance = (m) => {
  if (!isNum(m) || m <= 0) return DASH
  return m < 1000 ? `${Math.round(m)} m` : `${(m / 1000).toFixed(1)} km`
}

export const fmtMmSs = (s) => (isNum(s) ? formatMmSs(s) : DASH)

export const fmtSignedMmSs = (s) => {
  if (!isNum(s)) return DASH
  return (s < 0 ? '−' : '+') + formatMmSs(Math.abs(s))
}

export const fmtPct = (v) => (isNum(v) ? `${Math.round(v)}%` : DASH)

export const fmtKmh = (v) => (isNum(v) ? `${Math.round(v)} km/h` : DASH)

export const fmtCount = (v) => (isNum(v) ? v.toLocaleString('en-US') : DASH)

// Local wall-clock HH:mm of an ISO time
export const fmtClock = (iso) => {
  const d = iso ? new Date(iso) : null
  return d && !Number.isNaN(d.getTime())
    ? d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })
    : DASH
}

// Localised backend text {en, ar}: current language, else whichever exists
export const loc = (text, lang) => (text ? (text[lang] ?? text.en ?? text.ar ?? '') : '')

// Unicode bidi isolate (FSI … PDI): mixed Latin/Arabic text such as "C · شرق" is laid out as
// one unit, so what follows it ("· 10", "· 151 m") keeps its place in LTR and RTL contexts.
export const isolate = (text) => (text ? `\u2068${text}\u2069` : '')

// 'Use {route} instead' + { route: 'Route B' }
export const fmt = (template, vars = {}) =>
  String(template).replace(/\{(\w+)\}/g, (_, k) => (k in vars ? vars[k] : `{${k}}`))
