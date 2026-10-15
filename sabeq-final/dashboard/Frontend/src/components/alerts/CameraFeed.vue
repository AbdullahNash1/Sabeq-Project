<script setup>
// Drone camera: MJPEG stream (4:3) with detection boxes drawn from camera.detections.
import { computed, onUnmounted, ref } from 'vue'
import { useI18n } from '@/composables/useI18n'
import { useMissionView } from '@/composables/useMissionView'
import { useDashboardData } from '@/composables/useDashboardData'
import { useServerClock } from '@/composables/useServerClock'

const RETRY_MS = 3000
const STALE_MS = 1000

const { t } = useI18n()
const v = useMissionView()
const { drone, camera, detections, state } = useDashboardData()
const { localNow } = useServerClock()

// Stream: on error (or 503) show "unavailable" and retry with a fresh URL
const failed = ref(false)
const attempt = ref(0)
let retryTimer = 0
const streamSrc = computed(() => {
  const url = camera.value?.streamUrl
  if (!url) return ''
  return attempt.value ? `${url}${url.includes('?') ? '&' : '?'}retry=${attempt.value}` : url
})
const available = computed(() => !!camera.value?.available && !!streamSrc.value && !failed.value)
function onStreamError() {
  failed.value = true
  clearTimeout(retryTimer)
  retryTimer = setTimeout(() => {
    attempt.value++
    failed.value = false
  }, RETRY_MS)
}
onUnmounted(() => clearTimeout(retryTimer))

// Boxes from the latest detections; cleared if they stop arriving
const boxes = computed(() => {
  const d = detections.value
  if (!d || localNow.value - state.detectionsAt > STALE_MS) return []
  return d.boxes.map((b, i) => ({ key: i, ...b, conf: b.conf.toFixed(2) }))
})

// Per-route people counts during the scan (dynamic route list)
const counts = computed(() =>
  v.isScan.value
    ? v.routeCards.value.map((r) => ({ id: r.id, name: r.name, n: r.cnt, tone: r.tone }))
    : [],
)

const detector = computed(() => {
  const d = drone.value?.detector
  return d ? (d.fps ? `${d.model} · ${Math.round(d.fps)} FPS` : d.model) : ''
})
</script>

<template>
  <div class="feed" dir="ltr">
    <img
      v-if="available"
      :key="streamSrc"
      class="stream"
      :src="streamSrc"
      alt=""
      @error="onStreamError"
    />

    <div
      v-for="b in boxes"
      :key="b.key"
      class="det"
      :style="{ left: b.x + '%', top: b.y + '%', width: b.w + '%', height: b.h + '%' }"
    >
      <span class="det-tag">{{ b.conf }}</span>
    </div>

    <div v-if="counts.length" class="counts">
      <span
        v-for="c in counts"
        :key="c.id"
        class="count"
        :style="{ background: `var(--${c.tone})` }"
        >{{ c.name }} · {{ c.n }}</span
      >
    </div>

    <span class="badge rec"><span class="rec-dot" />{{ drone.id }} · {{ v.camMode.value }}</span>
    <span class="badge model">{{ detector }}</span>
    <span v-if="!available" class="sim">{{ t.camUnavailable }}</span>
  </div>
</template>

<style scoped>
.feed {
  position: relative;
  aspect-ratio: 4 / 3;
  border-radius: var(--r-lg);
  overflow: hidden;
  background: repeating-linear-gradient(135deg, var(--surf2) 0 10px, var(--ground) 10px 20px);
  border: 1px solid var(--line);
}

.stream {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: fill; /* boxes are in % of the 4:3 frame */
}

.det {
  --tone: var(--btn);
  position: absolute;
  border: 1.5px solid var(--tone);
  border-radius: 2px;
}

.det-tag {
  position: absolute;
  top: -13px;
  left: -1.5px;
  padding: 0 3px;
  background: var(--tone);
  color: var(--white);
  font: var(--fw-medium) 8.5px var(--font-mono);
  white-space: nowrap;
}

.counts {
  position: absolute;
  bottom: var(--sp-4);
  left: var(--sp-4);
  right: var(--sp-4);
  display: flex;
  justify-content: space-between;
  gap: var(--sp-3);
}

.count {
  padding: 3px 7px;
  border-radius: var(--r-xs);
  color: var(--white);
  font: var(--fw-semibold) var(--fs-xs) var(--font-mono);
}

.badge {
  position: absolute;
  top: var(--sp-4);
  padding: 3px 7px;
  border-radius: var(--r-xs);
  background: var(--overlay);
  color: var(--white);
  font: var(--fw-medium) 10.5px var(--font-mono);
}

.rec {
  left: var(--sp-4);
  display: flex;
  align-items: center;
  gap: 5px;
}

.rec-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--rec);
  animation: sbqBlink 1s infinite;
}

.model {
  right: var(--sp-4);
}

.sim {
  position: absolute;
  top: 50%;
  transform: translateY(-50%);
  left: 0;
  right: 0;
  text-align: center;
  font: var(--fw-regular) 10.5px var(--font-mono);
  color: var(--mut);
}
</style>
