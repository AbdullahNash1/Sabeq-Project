// The single WebSocket client (API_CONTRACT §5): snapshot on (re)connect replaces all
// local state, other messages patch it, ping every 5 s for latency + clock offset,
// reconnect with backoff.
import {
  applyMessage,
  applySnapshot,
  setClock,
  setConnection,
} from '@/composables/useDashboardData'
import { notifyError } from '@/composables/useNotices'

const PING_MS = 5000
const BACKOFF_MS = [500, 1000, 2000, 4000, 8000, 10000]

let ws = null
let attempt = 0
let pingTimer = 0
let started = false
const pings = new Map() // clientTs -> { sent: performance.now(), wall: Date.now() }

const url = () => `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`

export function connectSocket() {
  if (started) return
  started = true
  open()
}

function open() {
  setConnection(attempt === 0 ? 'connecting' : 'reconnecting')
  ws = new WebSocket(url())
  ws.onopen = () => {
    ping()
    pingTimer = setInterval(ping, PING_MS)
  }
  ws.onmessage = (event) => {
    let msg
    try {
      msg = JSON.parse(event.data)
    } catch {
      return
    }
    if (msg.type === 'snapshot') {
      attempt = 0
      applySnapshot(msg.data, msg.ts)
      setConnection('open')
    } else if (msg.type === 'pong') {
      onPong(msg)
    } else if (msg.type === 'error') {
      notifyError(msg.data.code, msg.data.message)
    } else {
      applyMessage(msg.type, msg.data)
    }
  }
  ws.onclose = () => {
    clearInterval(pingTimer)
    pings.clear()
    setConnection('reconnecting')
    const base = BACKOFF_MS[Math.min(attempt, BACKOFF_MS.length - 1)]
    attempt++
    setTimeout(open, base * (1 + Math.random() * 0.3))
  }
  // onerror is always followed by onclose
}

function ping() {
  if (ws?.readyState !== WebSocket.OPEN) return
  const clientTs = new Date().toISOString()
  pings.set(clientTs, { sent: performance.now(), wall: Date.now() })
  ws.send(JSON.stringify({ type: 'ping', data: { clientTs }, ts: clientTs }))
}

function onPong(msg) {
  const sent = pings.get(msg.data.clientTs)
  if (!sent) return
  pings.delete(msg.data.clientTs)
  const rtt = performance.now() - sent.sent
  const server = Date.parse(msg.ts)
  // NTP-style: the server stamped the pong roughly half a round trip after we sent.
  const offset = Number.isNaN(server) ? 0 : server - (sent.wall + rtt / 2)
  setClock(Math.round(rtt), offset)
}
