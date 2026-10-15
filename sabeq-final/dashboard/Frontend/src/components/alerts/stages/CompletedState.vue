<script setup>
// Stage "Completed": cart at the First Aid Center, drone landed on it; close the incident.
import AppButton from '@/components/ui/AppButton.vue'
import MetricTile from '@/components/ui/MetricTile.vue'
import { useI18n } from '@/composables/useI18n'
import { useMission } from '@/composables/useMission'
import { useMissionView } from '@/composables/useMissionView'

const { t } = useI18n()
const { close, pending } = useMission()
const { viaText, summary } = useMissionView()
</script>

<template>
  <div class="contents">
    <div class="banner">
      <span class="title">{{ t.completedT }}</span>
      <span class="sub">{{ viaText }} · {{ t.completedSub }}</span>
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
    <AppButton variant="primary" :disabled="!!pending" @click="close('resolved')">{{
      t.resolved
    }}</AppButton>
    <AppButton variant="outline" :disabled="!!pending" @click="close('transferred')">{{
      t.transferred
    }}</AppButton>
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

.summary {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--sp-5);
}
</style>
