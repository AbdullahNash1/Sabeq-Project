// Operator-facing error toasts (REST command errors, WS `error` messages).
import { reactive } from 'vue'
import { useI18n } from './useI18n'

const NOTICE_MS = 7000
const notices = reactive([])
let seq = 0

export function dismissNotice(id) {
  const i = notices.findIndex((n) => n.id === id)
  if (i >= 0) notices.splice(i, 1)
}

export function notifyError(code, message) {
  const { t } = useI18n()
  const id = ++seq
  notices.push({ id, title: t.value.errors[code] ?? t.value.errors.default, detail: message || '' })
  setTimeout(() => dismissNotice(id), NOTICE_MS)
}

export function useNotices() {
  return { notices, dismissNotice }
}
