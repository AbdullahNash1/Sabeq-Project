<script setup>
// Top bar: logo, site name, system status chips, clock, language and theme switches.
import { computed } from 'vue'
import SegmentedControl from '@/components/ui/SegmentedControl.vue'
import StatusBeat from './StatusBeat.vue'
import { useI18n } from '@/composables/useI18n'
import { useTheme } from '@/composables/useTheme'
import { useClock } from '@/composables/useClock'
import { useDashboardData } from '@/composables/useDashboardData'

const { t, lang, setLang } = useI18n()
const { theme, setTheme } = useTheme()
const { time } = useClock()
const { system, latencyMs, connection } = useDashboardData()

// Header chips: backend system.status + WS round trip measured by ping/pong
const beatItems = computed(() => {
  const s = system.value
  const wsUp = connection.value === 'open' && latencyMs.value != null
  return [
    { k: 'ROS 2', v: s?.ros.ok ? s.ros.label || 'OK' : '—', ok: !!s?.ros.ok },
    // detection rate (system.jetson in the API); in the simulation YOLO runs on the PC
    { k: t.value.beatYolo, v: s?.jetson.ok ? `${Math.round(s.jetson.fps)} FPS` : '—', ok: !!s?.jetson.ok },
    { k: 'WS', v: wsUp ? `${latencyMs.value} ms` : '—', ok: wsUp },
  ]
})

const langOptions = [
  { value: 'en', label: 'EN' },
  { value: 'ar', label: 'عربي' },
]
const themeOptions = computed(() => [
  { value: 'light', label: t.value.light },
  { value: 'dark', label: t.value.dark },
])
</script>

<template>
  <header class="app-header">
    <div class="brand-block">
      <div class="logo"><div class="logo-ring" /></div>
      <div class="stack">
        <span class="brand">{{ t.brand }}</span>
        <span class="sub">{{ t.tagline }}</span>
      </div>
      <div class="divider" />
      <div class="stack">
        <span class="site">{{ t.site }}</span>
        <span class="sub">{{ t.ops }}</span>
      </div>
    </div>

    <div class="spacer" />

    <div class="controls">
      <div class="beats">
        <StatusBeat v-for="b in beatItems" :key="b.k" :label="b.k" :value="b.v" :ok="b.ok" />
      </div>
      <span class="clock">{{ time }}</span>
      <SegmentedControl :model-value="lang" :options="langOptions" @update:model-value="setLang" />
      <SegmentedControl
        :model-value="theme"
        :options="themeOptions"
        @update:model-value="setTheme"
      />
    </div>
  </header>
</template>

<style scoped>
.app-header {
  display: flex;
  align-items: center;
  gap: var(--sp-10);
  padding: 0 var(--sp-10);
  background: var(--surf);
  border-bottom: 1px solid var(--line);
}

.brand-block {
  display: flex;
  align-items: center;
  gap: var(--sp-6);
  flex: none;
  white-space: nowrap;
}

.logo {
  width: 34px;
  height: 34px;
  border-radius: var(--r-md);
  background: var(--acc);
  display: grid;
  place-items: center;
  flex: none;
}

.logo-ring {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  border: 3px solid var(--white);
}

.stack {
  display: flex;
  flex-direction: column;
  line-height: 1.2;
}

.brand {
  font-weight: var(--fw-semibold);
  font-size: var(--fs-xl);
}

.site {
  font-size: var(--fs-md);
  font-weight: var(--fw-medium);
}

.sub {
  font-size: var(--fs-sm);
  color: var(--mut);
}

.divider {
  width: 1px;
  height: 28px;
  background: var(--line);
  margin: 0 var(--sp-3);
}

.spacer {
  flex: 1;
}

.controls {
  display: flex;
  align-items: center;
  gap: var(--sp-6);
  flex: none;
}

.beats {
  display: flex;
  gap: var(--sp-3);
}

.clock {
  font: var(--fw-medium) var(--fs-base) var(--font-mono);
  min-width: 72px;
  text-align: center;
}

/* Responsive: below the design's 1180px canvas */
@media (max-width: 1179px) {
  .app-header {
    flex-wrap: wrap;
    gap: var(--sp-6);
    padding: var(--sp-5) var(--sp-8);
  }
  .controls {
    flex-wrap: wrap;
    flex: 1 1 auto;
    justify-content: flex-end;
  }
}

@media (max-width: 767px) {
  .beats,
  .divider,
  .brand-block > .stack:last-child {
    display: none;
  }
  .spacer {
    display: none;
  }
  .controls {
    gap: var(--sp-4);
  }
}
</style>
