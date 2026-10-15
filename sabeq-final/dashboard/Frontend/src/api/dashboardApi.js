// REST commands (API_CONTRACT §4.4, §4.5). State never comes from these responses:
// the WebSocket (api/socket.js) is the only source of state.
import { request } from './http'

const post = (path, body = {}) => request('POST', path, body)
const mission = (id) => `/missions/${encodeURIComponent(id)}`

export const dashboardApi = {
  launch: (id) => post(`${mission(id)}/launch`),
  dismiss: (id) => post(`${mission(id)}/dismiss`),
  selectRoute: (id, route) => post(`${mission(id)}/route`, { route }),
  setPayload: (id, patch) => post(`${mission(id)}/payload`, patch),
  abort: (id, reason) => post(`${mission(id)}/abort`, { reason }),
  startReturn: (id) => post(`${mission(id)}/return`),
  close: (id, outcome) => post(`${mission(id)}/close`, outcome ? { outcome } : {}),
  devReset: () => post('/dev/sim/reset'),
}
