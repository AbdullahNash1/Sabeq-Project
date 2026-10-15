<script setup>
// Stage "Error": what failed, live drone status, and "Acknowledge & close".
import { computed } from 'vue'
import AppButton from '@/components/ui/AppButton.vue'
import DroneMarker from '@/components/ui/DroneMarker.vue'
import { useI18n } from '@/composables/useI18n'
import { useMission } from '@/composables/useMission'
import { useMissionView } from '@/composables/useMissionView'
import { useDashboardData } from '@/composables/useDashboardData'

const { t } = useI18n()
const { mission, close, pending } = useMission()
const { droneBatt } = useMissionView()
const { drone } = useDashboardData()

const error = computed(() => mission.value.error ?? { code: '', message: '' })
const headline = computed(() => t.value.errors[error.value.code] ?? t.value.errorT)
const droneState = computed(() =>
  drone.value.dockedOnCart
    ? t.value.droneDocked
    : (t.value.droneStates[drone.value.state] ?? drone.value.state),
)
</script>

<template>
  <div class="contents">
    <div class="banner" role="alert">
      <span class="title">{{ t.errorT }} · {{ headline }}</span>
      <span class="sub">{{ error.message }}</span>
      <span v-if="error.code" class="code">{{ error.code }}</span>
    </div>
    <div class="unit">
      <span class="unit-name"
        ><DroneMarker /><span class="mono strong">{{ drone.id }}</span></span
      >
      <span class="muted">{{ droneState }} · {{ drone.link }} · {{ droneBatt }}%</span>
    </div>
    <AppButton variant="danger" :disabled="!!pending" @click="close()">{{ t.ackClose }}</AppButton>
  </div>
</template>

<style scoped>
.contents {
  display: contents;
}

.banner {
  padding: var(--sp-9);
  border-radius: var(--r-xl);
  background: var(--redSoft);
  border: 1.5px solid var(--red);
  display: flex;
  flex-direction: column;
  gap: var(--sp-2);
  animation: sbqIn 0.35s ease-out;
}

.title {
  font-size: var(--fs-xl);
  font-weight: var(--fw-semibold);
  color: var(--red);
}

.sub {
  font-size: var(--fs-md);
  color: var(--ink);
  overflow-wrap: anywhere;
}

.code {
  font: var(--fw-medium) var(--fs-xs) var(--font-mono);
  color: var(--mut);
}

.unit {
  display: flex;
  justify-content: space-between;
  gap: var(--sp-5);
  padding: 11px var(--sp-7);
  border: 1px solid var(--line);
  border-radius: var(--r-lg);
  font-size: var(--fs-md);
}

.unit-name {
  display: flex;
  align-items: center;
  gap: var(--sp-4);
}

.mono {
  font-family: var(--font-mono);
}

.strong {
  font-weight: var(--fw-semibold);
}

.muted {
  color: var(--mut);
  text-align: end;
}
</style>
