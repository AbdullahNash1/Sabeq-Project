<script setup>
// Red card describing the active incident, with Launch / False alarm buttons.
import { computed } from 'vue'
import AppButton from '@/components/ui/AppButton.vue'
import MetricTile from '@/components/ui/MetricTile.vue'
import { useI18n } from '@/composables/useI18n'
import { useMission } from '@/composables/useMission'
import { useMissionView } from '@/composables/useMissionView'
import { useDashboardData } from '@/composables/useDashboardData'
import { useServerClock } from '@/composables/useServerClock'
import { fmtClock, fmtDistance } from '@/utils/format'

const { t, l, fmt } = useI18n()
const { launch, dismiss, pending } = useMission()
const { isAlert, inFlight, stageLabel, mission } = useMissionView()
const { incident } = useDashboardData()
const { serverNow } = useServerClock()

// "Just now" for the first minute, then the report time
const reportedText = computed(() => {
  const at = Date.parse(incident.value?.reportedAt ?? '')
  return serverNow.value - at < 60_000 ? t.value.justNow : fmtClock(incident.value?.reportedAt)
})
const severity = computed(
  () => t.value.severity[incident.value?.severity] ?? incident.value?.severity,
)
const source = computed(() => t.value.sources[incident.value?.source] ?? '')
const launchSub = computed(() =>
  fmt(t.value.launchSub, { alt: mission.value?.scanAltitudeM ?? 8 }),
)
</script>

<template>
  <div v-if="incident" class="card" :class="{ outlined: isAlert }">
    <div class="head">
      <span class="severity"><span class="dot" />{{ severity }}</span>
      <span class="time">{{ reportedText }}</span>
    </div>

    <div class="body">
      <span class="title">{{ l(incident.type.label) }}</span>
      <span class="loc">{{ l(incident.location.description) }}</span>
      <span class="src">{{ source }}</span>
    </div>

    <template v-if="isAlert">
      <div class="facts">
        <MetricTile
          size="sm"
          filled
          :label="t.distHub"
          :value="fmtDistance(incident.distanceFromHubM)"
        />
        <MetricTile size="sm" filled :label="t.scanAlt" :value="`${mission.scanAltitudeM} m`" />
      </div>
      <AppButton variant="primary" class="launch" :disabled="!!pending" @click="launch">
        <span class="launch-title">{{ t.launch }}</span>
        <span class="launch-sub">{{ launchSub }}</span>
      </AppButton>
      <AppButton variant="quiet" :disabled="!!pending" @click="dismiss">{{
        t.falseAlarm
      }}</AppButton>
    </template>

    <div v-if="inFlight" class="mission-row">
      <span class="mission-id">{{ mission.id }}</span>
      <span class="muted">·</span>
      <span class="stage">{{ stageLabel }}</span>
    </div>
  </div>
</template>

<style scoped>
.card {
  padding: var(--sp-8);
  border-radius: var(--r-xl);
  background: var(--redSoft);
  border: 1.5px solid transparent;
  display: flex;
  flex-direction: column;
  gap: var(--sp-6);
  animation: sbqIn 0.35s ease-out;
}

.outlined {
  border-color: var(--red);
}

.head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--sp-4);
}

.severity {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  font: var(--fw-semibold) var(--fs-xs) var(--font-mono);
  color: var(--red);
  letter-spacing: 0.06em;
  text-transform: uppercase;
}

.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--red);
  animation: sbqBlink 1.2s ease-in-out infinite;
}

.time {
  font: var(--fw-regular) var(--fs-sm) var(--font-mono);
  color: var(--mut);
}

.body {
  display: flex;
  flex-direction: column;
  gap: var(--sp-2);
}

.title {
  font-size: var(--fs-2xl);
  font-weight: var(--fw-semibold);
}

.loc {
  font-size: var(--fs-md);
  color: var(--mut);
}

.src {
  font-size: var(--fs-sm);
  color: var(--mut);
}

.facts {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: var(--sp-4);
}

.launch.primary {
  padding: var(--sp-7) var(--sp-8);
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: var(--sp-1);
  text-align: start;
}

.launch-title {
  font-size: var(--fs-lg);
  font-weight: var(--fw-semibold);
}

.launch-sub {
  font-size: var(--fs-sm);
  font-weight: var(--fw-regular);
  opacity: 0.9;
}

.mission-row {
  display: flex;
  align-items: center;
  gap: var(--sp-4);
  padding-top: var(--sp-5);
  border-top: 1px solid var(--line);
  font-size: var(--fs-sm);
}

.mission-id {
  font: var(--fw-semibold) var(--fs-sm) var(--font-mono);
}

.stage {
  color: var(--accDeep);
  font-weight: var(--fw-medium);
}
</style>
