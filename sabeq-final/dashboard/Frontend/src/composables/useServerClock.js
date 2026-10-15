// Ticking "now" in server time (browser clock + measured offset), for countdowns and timers.
import { computed, ref } from 'vue'
import { useDashboardData } from './useDashboardData'

const localNow = ref(Date.now())
let timer = 0

export function useServerClock() {
  if (!timer) timer = setInterval(() => (localNow.value = Date.now()), 100)
  const { state } = useDashboardData()
  const serverNow = computed(() => localNow.value + state.clockOffsetMs)
  return { localNow, serverNow }
}
