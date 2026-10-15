<script setup>
// List of recently closed missions (last 10 kept by the backend).
import { computed } from 'vue'
import { useI18n } from '@/composables/useI18n'
import { useDashboardData } from '@/composables/useDashboardData'
import { fmtClock, fmtMmSs, isolate } from '@/utils/format'

const props = defineProps({
  limit: { type: Number, default: 2 },
})

const { t, l } = useI18n()
const { recentMissions } = useDashboardData()

const zoneName = (zone) => t.value.zShort['ABCD'.indexOf(zone)] ?? zone

const items = computed(() =>
  recentMissions.value.slice(0, props.limit).map((r) => ({
    id: r.id,
    type: l(r.incidentType.label),
    // The route's label in the current language ("C · East" / "C · شرق"); the zone when the
    // mission never got a route
    where: r.routeLabel ? isolate(l(r.routeLabel)) : zoneName(r.zone),
    time: fmtClock(r.reportedAt),
    resp: fmtMmSs(r.responseTimeS),
  })),
)
</script>

<template>
  <div class="recent">
    <span v-if="items.length" class="eyebrow">{{ t.recent }}</span>
    <div v-for="r in items" :key="r.id" class="item">
      <div class="info">
        <span class="type">{{ r.type }}</span>
        <span class="meta">{{ r.where }} · {{ r.time }}</span>
      </div>
      <span class="resp">{{ r.resp }}</span>
    </div>
  </div>
</template>

<style scoped>
.recent {
  display: flex;
  flex-direction: column;
  gap: var(--sp-4);
}

.item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--sp-5);
  padding: var(--sp-6) var(--sp-7);
  border: 1px solid var(--line);
  border-radius: var(--r-lg);
  font-size: var(--fs-md);
}

.info {
  display: flex;
  flex-direction: column;
  gap: var(--sp-1);
}

.type {
  font-weight: var(--fw-medium);
}

.meta {
  color: var(--mut);
  font-size: var(--fs-sm);
}

.resp {
  font: var(--fw-medium) var(--fs-sm) var(--font-mono);
  color: var(--accDeep);
}
</style>
