import { computed, onMounted, onUnmounted, ref } from 'vue'

// Live wall clock, updated once per second while the caller is mounted.
export function useClock() {
  const now = ref(new Date())
  let timer

  onMounted(() => {
    timer = setInterval(() => (now.value = new Date()), 1000)
  })
  onUnmounted(() => clearInterval(timer))

  const time = computed(() => now.value.toLocaleTimeString('en-GB'))

  return { now, time }
}
