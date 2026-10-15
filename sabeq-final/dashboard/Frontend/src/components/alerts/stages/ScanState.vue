<script setup>
// Stage "Scan": live telemetry, camera, route comparison, pre-selection / override, auto-select countdown.
import AppButton from '@/components/ui/AppButton.vue'
import MetricTile from '@/components/ui/MetricTile.vue'
import ProgressBar from '@/components/ui/ProgressBar.vue'
import CameraFeed from '@/components/alerts/CameraFeed.vue'
import RouteCompareCard from '@/components/alerts/RouteCompareCard.vue'
import { useI18n } from '@/composables/useI18n'
import { useMission } from '@/composables/useMission'
import { useMissionView } from '@/composables/useMissionView'

const { t } = useI18n()
const { selectRoute, abort, pending } = useMission()
const {
  scanStatus,
  telemetry,
  routeCards,
  routeButtons,
  autoSelect,
  countdown,
  autoText,
  preselectText,
} = useMissionView()
</script>

<template>
  <div class="contents">
    <div class="heading">
      <span class="title">{{ t.scanning }}</span>
      <span class="status">{{ scanStatus }}</span>
    </div>
    <div class="telemetry">
      <MetricTile v-for="m in telemetry" :key="m.k" :label="m.k" :value="m.v" />
    </div>

    <CameraFeed />

    <template v-if="routeCards.length">
      <div class="routes" :style="{ '--n': Math.max(2, Math.min(routeCards.length, 3)) }">
        <RouteCompareCard v-for="r in routeCards" :key="r.id" :route="r" />
      </div>
      <span class="formula">{{ t.formula }}</span>
    </template>

    <div v-if="preselectText" class="auto">
      <span class="auto-title">{{ t.preselected }}</span>
      <span class="auto-sub">{{ preselectText }}</span>
    </div>
    <div v-else-if="autoSelect" class="auto">
      <span class="auto-title">{{ autoText }}</span>
      <span class="auto-sub">{{ t.autoSub }} {{ countdown }} {{ t.sec }}</span>
      <ProgressBar :value="autoSelect.holdPct" :height="4" track="var(--surf)" />
    </div>

    <div v-if="routeButtons.length" class="choices">
      <AppButton
        v-for="b in routeButtons"
        :key="b.id"
        :variant="b.variant"
        :disabled="!!pending"
        @click="selectRoute(b.id)"
        >{{ b.label }}</AppButton
      >
    </div>

    <AppButton variant="danger" :disabled="!!pending" @click="abort('operator_abort')">{{
      t.abort
    }}</AppButton>
  </div>
</template>

<style scoped>
.contents {
  display: contents;
}

.heading {
  display: flex;
  flex-direction: column;
  gap: var(--sp-2);
}

.title {
  font-size: var(--fs-xl);
  font-weight: var(--fw-semibold);
}

.status {
  font-size: var(--fs-md);
  color: var(--mut);
}

.telemetry {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: var(--sp-4);
}

.routes {
  display: grid;
  grid-template-columns: repeat(var(--n), minmax(0, 1fr));
  gap: var(--sp-5);
}

.choices {
  display: flex;
  flex-direction: column;
  gap: var(--sp-4);
}

.formula {
  font: var(--fw-regular) 11.5px/1.5 var(--font-mono);
  color: var(--mut);
  text-wrap: pretty;
}

.auto {
  padding: var(--sp-7) var(--sp-8);
  border-radius: var(--r-lg);
  background: var(--accSoft);
  display: flex;
  flex-direction: column;
  gap: var(--sp-4);
  animation: sbqIn 0.3s ease-out;
}

.auto-title {
  font-size: var(--fs-base);
  font-weight: var(--fw-semibold);
}

.auto-sub {
  font-size: 12.5px;
  color: var(--mut);
}
</style>
