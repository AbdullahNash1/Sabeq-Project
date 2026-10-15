<script setup>
// Right-hand panel. Shows a different stage component depending on the mission stage.
import IdleState from '@/components/alerts/stages/IdleState.vue'
import IncidentCard from './IncidentCard.vue'
import ScanState from '@/components/alerts/stages/ScanState.vue'
import EscortState from '@/components/alerts/stages/EscortState.vue'
import ArrivedState from '@/components/alerts/stages/ArrivedState.vue'
import CompletedState from '@/components/alerts/stages/CompletedState.vue'
import AbortedState from '@/components/alerts/stages/AbortedState.vue'
import ErrorState from '@/components/alerts/stages/ErrorState.vue'
import { computed } from 'vue'
import { useI18n } from '@/composables/useI18n'
import { useMissionView } from '@/composables/useMissionView'
import { useDashboardData } from '@/composables/useDashboardData'

const { t } = useI18n()
const v = useMissionView()
const { isIdle, hasMission, inFlight, isScan, isArrived } = v
const { openIncidents } = useDashboardData()
// Active + queued (there is no queued-incident list in v1)
const alertCount = computed(() => openIncidents.value.active + openIncidents.value.queued)
</script>

<template>
  <aside class="alerts" :aria-label="t.alerts">
    <div class="head">
      <div class="title-wrap">
        <span class="title">{{ t.alerts }}</span>
        <span class="count" :class="{ zero: alertCount === 0 }">{{ alertCount }}</span>
      </div>
      <div class="tabs">
        <span class="tab active">{{ t.active }}</span>
      </div>
    </div>

    <div class="body">
      <IdleState v-if="isIdle" />
      <template v-if="hasMission">
        <IncidentCard />
        <div v-if="inFlight" class="divider" />
        <ScanState v-if="isScan" />
        <EscortState v-if="v.isEscort.value || v.isReturning.value" />
        <ArrivedState v-if="isArrived" />
        <CompletedState v-if="v.isCompleted.value" />
        <AbortedState v-if="v.isAborted.value" />
        <ErrorState v-if="v.isError.value" />
      </template>
    </div>
  </aside>
</template>

<style scoped>
.alerts {
  background: var(--surf);
  border-inline-start: 1px solid var(--line);
  overflow-y: auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.head {
  position: sticky;
  top: 0;
  z-index: 2;
  background: var(--surf);
  padding: var(--sp-9) var(--sp-10) var(--sp-7);
  border-bottom: 1px solid var(--line);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sp-5);
}

.title-wrap {
  display: flex;
  align-items: center;
  gap: var(--sp-5);
}

.title {
  font-size: var(--fs-2xl);
  font-weight: var(--fw-semibold);
}

.count {
  min-width: 22px;
  height: 22px;
  padding: 0 7px;
  box-sizing: border-box;
  border-radius: 11px;
  display: grid;
  place-items: center;
  background: var(--red);
  color: var(--white);
  font: var(--fw-semibold) var(--fs-sm) var(--font-mono);
}

.count.zero {
  background: var(--surf2);
  color: var(--mut);
}

.tabs {
  display: flex;
  gap: var(--sp-1);
  padding: 3px;
  background: var(--surf2);
  border: 1px solid var(--line);
  border-radius: 9px;
  font-size: var(--fs-sm);
}

.tab {
  padding: var(--sp-2) var(--sp-5);
  border-radius: 6px;
}

.tab.active {
  background: var(--surf);
  box-shadow: var(--shadow);
  font-weight: var(--fw-medium);
}

.body {
  padding: var(--sp-8) var(--sp-10) 28px;
  display: flex;
  flex-direction: column;
  gap: var(--sp-8);
}

.divider {
  height: 1px;
  background: var(--line);
  margin: var(--sp-2) 0;
}
</style>
