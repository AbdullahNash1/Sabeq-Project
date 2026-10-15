<script setup>
// One route card comparing length, crowd and estimated time. `name` is the route's label.
import ProgressBar from '@/components/ui/ProgressBar.vue'
import { useI18n } from '@/composables/useI18n'

defineProps({
  route: { type: Object, required: true }, // { name, len, cnt, est, rec, pre, barPct, tone }
})

const { t } = useI18n()
</script>

<template>
  <div class="card" :class="{ rec: route.rec, pre: route.pre }">
    <div class="head">
      <span class="name">{{ route.name }}</span>
      <span class="badges">
        <span v-if="route.pre" class="badge pre-badge">{{ t.preselected }}</span>
        <span v-if="route.rec" class="badge">{{ t.recommended }}</span>
      </span>
    </div>
    <div class="facts">
      <span class="muted">{{ t.length }}</span
      ><span class="val">{{ route.len }}</span> <span class="muted">{{ t.crowd }}</span
      ><span class="val">{{ route.cnt }} {{ t.people }}</span>
    </div>
    <ProgressBar :value="route.barPct" :height="5" :color="`var(--${route.tone})`" />
    <div class="foot">
      <span class="foot-label">{{ t.estTime }}</span>
      <span class="est">{{ route.est }}</span>
    </div>
  </div>
</template>

<style scoped>
.card {
  padding: var(--sp-6);
  border-radius: var(--r-lg);
  border: 1px solid var(--line);
  display: flex;
  flex-direction: column;
  gap: var(--sp-4);
  min-width: 0;
}

.card.rec {
  border: 1.5px solid var(--acc);
  background: var(--accSoft);
}

.head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--sp-3);
  min-height: 22px;
  flex-wrap: wrap;
}

.name {
  white-space: nowrap;
  font-weight: var(--fw-semibold);
  font-size: var(--fs-base);
}

.card.pre {
  border: 1.5px solid var(--ink);
}

.badges {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: var(--sp-2);
}

.pre-badge {
  background: var(--ink);
  color: var(--surf);
}

.badge {
  padding: 2px 7px;
  border-radius: 6px;
  background: var(--btn);
  color: var(--btnInk);
  font-size: var(--fs-xs);
  font-weight: var(--fw-semibold);
}

.facts {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: var(--sp-3) var(--sp-4);
  font-size: var(--fs-sm);
}

.val {
  text-align: end;
  font-family: var(--font-mono);
}

.foot {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  padding-top: var(--sp-3);
  border-top: 1px solid var(--line);
}

.foot-label {
  font-size: var(--fs-sm);
  color: var(--mut);
}

.est {
  font: var(--fw-semibold) var(--fs-3xl) var(--font-mono);
}
</style>
