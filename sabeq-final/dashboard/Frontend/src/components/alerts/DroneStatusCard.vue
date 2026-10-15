<script setup>
// Drone readiness card shown when nothing is happening.
import ProgressBar from '@/components/ui/ProgressBar.vue'
import DroneMarker from '@/components/ui/DroneMarker.vue'
import { computed } from 'vue'
import { useI18n } from '@/composables/useI18n'
import { useDashboardData } from '@/composables/useDashboardData'

const { t } = useI18n()
const { drone } = useDashboardData()

const ready = computed(() => drone.value.dockedOnCart && drone.value.link !== 'lost')
const status = computed(() =>
  drone.value.link === 'lost'
    ? t.value.linkLost
    : ready.value
      ? t.value.dockReady
      : t.value.notReady,
)
const battery = computed(() => Math.round(drone.value.batteryPct))
const flightLeft = computed(() => Math.round(drone.value.flightTimeLeftS / 60))
// Healthy payload hardware, e.g. "Cam · Speaker · LED"
const payload = computed(() => {
  const p = drone.value.payload
  return (
    [p.camera && 'Cam', p.speaker && 'Speaker', p.led && 'LED'].filter(Boolean).join(' · ') || '—'
  )
})
</script>

<template>
  <div class="card">
    <div class="row">
      <span class="id-wrap"
        ><DroneMarker /><span class="id">{{ drone.id }}</span></span
      >
      <span class="pill" :class="{ warn: !ready }">{{ status }}</span>
    </div>

    <div class="battery">
      <div class="battery-row">
        <span class="muted label">{{ t.battery }}</span>
        <span class="battery-val">{{ battery }}%</span>
      </div>
      <ProgressBar :value="battery" :height="6" />
    </div>

    <div class="facts">
      <span class="muted">{{ t.location }}</span
      ><span class="end">{{ drone.dockedOnCart ? t.cartDock : t.airborne }}</span>
      <span class="muted">{{ t.flightLeft }}</span
      ><span class="end mono">{{ flightLeft }} {{ t.min }}</span>
      <span class="muted">{{ t.payload }}</span
      ><span class="end">{{ payload }}</span>
    </div>
  </div>
</template>

<style scoped>
.card {
  padding: var(--sp-8);
  border: 1px solid var(--line);
  border-radius: var(--r-xl);
  display: flex;
  flex-direction: column;
  gap: var(--sp-7);
}

.row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--sp-4);
}

.id-wrap {
  display: flex;
  align-items: center;
  gap: var(--sp-4);
}

.id {
  font: var(--fw-semibold) var(--fs-base) var(--font-mono);
}

.pill {
  padding: 3px 9px;
  border-radius: var(--r-sm);
  background: var(--accSoft);
  color: var(--accDeep);
  font-size: var(--fs-sm);
  font-weight: var(--fw-medium);
}
.pill.warn {
  background: var(--ambSoft);
  color: var(--amb);
}

.battery {
  display: flex;
  flex-direction: column;
  gap: var(--sp-3);
}

.battery-row {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
}

.label {
  font-size: var(--fs-sm);
}

.battery-val {
  font: var(--fw-semibold) var(--fs-3xl) var(--font-mono);
}

.facts {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: var(--sp-4) var(--sp-7);
  font-size: var(--fs-md);
}

.end {
  text-align: end;
}
</style>
