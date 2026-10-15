<script setup>
// Stage "Aborted": reason, the drone landing back on the cart dock, manual drive note, Close.
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

const reason = computed(() => t.value.abortReasons[mission.value.abortReason] ?? '')
const docked = computed(() => drone.value.dockedOnCart)
</script>

<template>
  <div class="contents">
    <div class="banner">
      <span class="title">{{ t.abortedT }}</span>
      <span class="sub">{{ reason }}</span>
    </div>
    <div class="unit">
      <span class="unit-name"
        ><DroneMarker /><span class="mono strong">{{ drone.id }}</span></span
      >
      <span class="muted">{{ docked ? t.droneDocked : t.droneReturning }} · {{ droneBatt }}%</span>
    </div>
    <span class="note">{{ t.manualDrive }}</span>
    <AppButton variant="primary" :disabled="!!pending" @click="close()">{{ t.close }}</AppButton>
  </div>
</template>

<style scoped>
.contents {
  display: contents;
}

.banner {
  padding: var(--sp-9);
  border-radius: var(--r-xl);
  background: var(--ambSoft);
  display: flex;
  flex-direction: column;
  gap: var(--sp-2);
  animation: sbqIn 0.35s ease-out;
}

.title {
  font-size: var(--fs-xl);
  font-weight: var(--fw-semibold);
}

.sub,
.note {
  font-size: var(--fs-md);
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
