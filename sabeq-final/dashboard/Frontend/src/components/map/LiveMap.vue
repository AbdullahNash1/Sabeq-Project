<script setup>
// The big map card: title, scalable map canvas, legend and zoom buttons.
import SiteMapBase from './SiteMapBase.vue'
import MissionOverlay from './MissionOverlay.vue'
import MapLabels from './MapLabels.vue'
import MapLegend from './MapLegend.vue'
import ZoomControls from './ZoomControls.vue'
import { useI18n } from '@/composables/useI18n'
import { useMapZoom } from '@/composables/useMapZoom'
import { useDashboardData } from '@/composables/useDashboardData'
import { VIEWBOX, metres } from '@/data/siteMap'
import { fmtCount } from '@/utils/format'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

const { t } = useI18n()
const { zoom, zoomIn, zoomOut, zoomStyle } = useMapZoom()
const { site } = useDashboardData()

// Scale bar drawn to scale: scaleBarM metres = metres(scaleBarM) SVG units (API_CONTRACT §2,
// 3.5 units/m), times the rendered width of one SVG unit, times the map zoom.
const canvas = ref(null)
const canvasWidth = ref(0)
let observer = null
onMounted(() => {
  observer = new ResizeObserver(([entry]) => (canvasWidth.value = entry.contentRect.width))
  observer.observe(canvas.value)
})
onBeforeUnmount(() => observer?.disconnect())
const scaleBarStyle = computed(() => {
  const px = (canvasWidth.value / VIEWBOX.w) * metres(site.value?.map?.scaleBarM ?? 0) * zoom.value
  return { width: `${px.toFixed(1)}px` }
})
</script>

<template>
  <section class="live-map" :aria-label="t.liveMap">
    <!-- Map is always laid out LTR; geography doesn't mirror in Arabic -->
    <div class="stage" dir="ltr">
      <div class="zoom-layer" :style="zoomStyle">
        <div class="fit">
          <div ref="canvas" class="canvas">
            <svg
              :viewBox="`0 0 ${VIEWBOX.w} ${VIEWBOX.h}`"
              preserveAspectRatio="xMidYMid meet"
              class="svg"
            >
              <SiteMapBase />
              <MissionOverlay />
            </svg>
            <MapLabels />
          </div>
        </div>
      </div>
    </div>

    <div class="heading">
      <span class="title">{{ t.liveMap }}</span>
      <span class="meta">
        <span>{{ fmtCount(site.visitors) }} {{ t.visitors }}</span>
        <span class="scale"><span class="scale-bar" :style="scaleBarStyle" />{{ site.map.scaleBarM }} m</span>
      </span>
    </div>

    <MapLegend />
    <ZoomControls @zoom-in="zoomIn" @zoom-out="zoomOut" />
  </section>
</template>

<style scoped>
.live-map {
  position: relative;
  flex: 1;
  min-height: 0;
  margin: var(--sp-8) var(--sp-8) var(--sp-6);
  border-radius: var(--r-2xl);
  background: var(--surf);
  border: 1px solid var(--line);
  overflow: hidden;
  box-shadow: var(--shadow);
}

.stage {
  position: absolute;
  inset: 60px var(--sp-8) 64px var(--sp-8);
  overflow: hidden;
}

.zoom-layer {
  width: 100%;
  height: 100%;
  transform-origin: 60% 45%;
  transition: transform 0.3s ease;
}

.fit {
  width: 100%;
  height: 100%;
  container-type: size;
  display: grid;
  place-items: center;
}

.canvas {
  position: relative;
  width: min(100cqw, 156.25cqh);
  aspect-ratio: 1000 / 640;
  container-type: inline-size;
}

.svg {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  display: block;
  overflow: visible;
}

.heading {
  position: absolute;
  top: var(--sp-7);
  inset-inline-start: var(--sp-9);
  display: flex;
  flex-direction: column;
  gap: var(--sp-1);
}

.title {
  font-size: var(--fs-lg);
  font-weight: var(--fw-semibold);
}

.meta {
  display: flex;
  align-items: center;
  gap: var(--sp-5);
  font: var(--fw-regular) var(--fs-sm) var(--font-mono);
  color: var(--mut);
}

.scale {
  display: flex;
  align-items: center;
  gap: 5px;
}

.scale-bar {
  flex: none;
  height: 4px;
  border: 1.5px solid var(--mut);
  border-top: 0;
  box-sizing: border-box;
}

@media (max-width: 1179px) {
  .live-map {
    flex: none;
    height: min(72vh, 640px);
  }
}

@media (max-width: 767px) {
  .live-map {
    margin: var(--sp-6);
    height: auto;
    aspect-ratio: 1000 / 900;
  }
  .stage {
    inset: 56px var(--sp-4) 110px var(--sp-4);
  }
  :deep(.legend) {
    max-width: calc(100% - 80px);
    gap: var(--sp-4) var(--sp-5);
    padding: var(--sp-4) var(--sp-5);
    font-size: var(--fs-xs);
  }
}
</style>
