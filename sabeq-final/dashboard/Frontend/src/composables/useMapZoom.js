// Zoom level for the map and the zoom in / out actions.
import { computed, ref } from 'vue'

export function useMapZoom({ min = 1, max = 1.8, step = 0.2 } = {}) {
  const zoom = ref(min)

  const zoomIn = () => (zoom.value = Math.min(max, +(zoom.value + step).toFixed(1)))
  const zoomOut = () => (zoom.value = Math.max(min, +(zoom.value - step).toFixed(1)))

  const zoomStyle = computed(() => ({ transform: `scale(${zoom.value})` }))

  return { zoom, zoomIn, zoomOut, zoomStyle }
}
