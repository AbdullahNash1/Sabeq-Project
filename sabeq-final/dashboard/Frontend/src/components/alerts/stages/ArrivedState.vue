<script setup>
import { computed } from 'vue'
// Stage "Arrived": arrival banner, rescue progress and mission summary.
// Return starts automatically after the rescue sequence loads the patient.
import MetricTile from '@/components/ui/MetricTile.vue'
import { useI18n } from '@/composables/useI18n'
import { useMissionView } from '@/composables/useMissionView'
import { useDashboardData } from '@/composables/useDashboardData'

const { t } = useI18n()
const { viaText, summary, patientLoaded } = useMissionView()
const { drone, cart } = useDashboardData()
const autoReturning = computed(() => cart.value?.state === 'to_hub' || cart.value?.state === 'at_hub')
const stageTitle = computed(() =>
  cart.value?.state === 'at_hub' ? t.value.completedT : autoReturning.value ? t.value.returningT : t.value.arrivedT,
)
</script>

<template>
  <div class="contents">
    <div class="banner">
      <span class="title">{{ stageTitle }}</span>
      <span class="sub">{{ viaText }} · {{ drone.id }}</span>
    </div>
    <div class="rescue" :class="{ done: patientLoaded }" role="status">
      <span v-if="patientLoaded" class="rescue-icon">✓</span>
      <span v-else class="spinner" aria-hidden="true" />
      <span class="rescue-text">
        <span class="rescue-title">{{ patientLoaded ? t.rescueLoaded : t.rescueInProgress }}</span>
        <span class="rescue-sub">{{ patientLoaded ? t.rescueLoadedSub : t.rescueInProgressSub }}</span>
      </span>
    </div>
    <div class="summary">
      <MetricTile
        v-for="s in summary"
        :key="s.k"
        size="lg"
        :label="s.k"
        :value="s.v"
        :tone="s.tone"
      />
    </div>
  </div>
</template>

<style scoped>
.contents {
  display: contents;
}

.banner {
  padding: var(--sp-9);
  border-radius: var(--r-xl);
  background: var(--accSoft);
  display: flex;
  flex-direction: column;
  gap: var(--sp-2);
  animation: sbqIn 0.35s ease-out;
}

.title {
  font-size: var(--fs-xl);
  font-weight: var(--fw-semibold);
}

.sub {
  font-size: var(--fs-md);
  color: var(--mut);
}

.rescue {
  display: flex;
  align-items: center;
  gap: var(--sp-6);
  padding: var(--sp-7) var(--sp-8);
  border-radius: var(--r-lg);
  border: 1px solid var(--line);
}
.rescue.done {
  border-color: var(--acc);
  background: var(--accSoft);
}
.rescue-text {
  display: flex;
  flex-direction: column;
  gap: var(--sp-1);
}
.rescue-title {
  font-weight: var(--fw-semibold);
}
.rescue-sub {
  font-size: var(--fs-sm);
  color: var(--mut);
}
.rescue-icon {
  width: 22px;
  height: 22px;
  border-radius: 50%;
  display: grid;
  place-items: center;
  flex: none;
  background: var(--acc);
  color: var(--white);
  font-weight: var(--fw-semibold);
}
.spinner {
  width: 22px;
  height: 22px;
  flex: none;
  border-radius: 50%;
  border: 3px solid var(--line);
  border-top-color: var(--acc);
  animation: sbqSpin 0.9s linear infinite;
}
@keyframes sbqSpin {
  to {
    transform: rotate(360deg);
  }
}
@media (prefers-reduced-motion: reduce) {
  .spinner {
    animation-duration: 3s;
  }
}
.summary {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--sp-5);
}
</style>
