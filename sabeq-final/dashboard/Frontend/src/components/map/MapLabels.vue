<script setup>
// Text labels placed over the map (places, route tags, scan sector chips).
import { useI18n } from '@/composables/useI18n'
import { computed } from 'vue'
import { useMissionView } from '@/composables/useMissionView'
import { VIEWBOX, placeLabels } from '@/data/siteMap'

const { t } = useI18n()
const v = useMissionView()

// "Incident" tag just above the incident marker
const incidentTagPos = computed(() => {
  const p = v.incidentPoint.value
  return p && { left: `${(p[0] / VIEWBOX.w) * 100}%`, top: `${((p[1] - 30) / VIEWBOX.h) * 100}%` }
})
</script>

<template>
  <!-- Sizes use container query units so labels scale with the map -->
  <span
    v-for="p in placeLabels"
    :key="p.key"
    class="lbl place"
    :class="[p.tone, 'anchor-' + p.anchor]"
    :style="p.style"
    >{{ t[p.key] }}</span
  >

  <!-- While scanning: one chip per sector (label · live count); afterwards the route tags -->
  <template v-if="v.state.value === 'scanning'">
    <span
      v-for="s in v.sectors.value"
      :key="s.id"
      class="lbl centered sector-chip"
      :class="{ active: s.active }"
      :style="{ ...s.chipPos, '--tone': `var(--${s.tone})` }"
      >{{ s.chip }}</span
    >
  </template>
  <template v-else-if="v.hasMission.value">
    <span
      v-for="r in v.routeLines.value"
      :key="r.id"
      class="lbl centered route-tag"
      :class="v.routeVariants.value[r.id]"
      :style="r.labelPos"
      >{{ r.label }}</span
    >
  </template>

  <span v-if="v.isScan.value" class="lbl alt-tag" :style="v.altTagPos.value">{{
    v.altTag.value
  }}</span>
  <span v-if="incidentTagPos" class="lbl centered incident-tag" :style="incidentTagPos">{{
    t.incident
  }}</span>
</template>

<style scoped>
.lbl {
  position: absolute;
  white-space: nowrap;
  pointer-events: none;
  font-family: var(--font-sans);
}

.centered {
  transform: translate(-50%, -50%);
}

.place {
  font: var(--fw-semibold) max(7px, 1.1cqw) var(--font-sans);
}
.place.site {
  color: var(--ink);
}
.place.aid {
  color: var(--red);
}
.anchor-start {
  transform: translateY(-50%);
}
.anchor-end {
  transform: translate(-100%, -50%);
}
.anchor-center {
  transform: translate(-50%, -50%);
}

.sector-chip {
  padding: 0.3cqw 1cqw;
  border-radius: 2cqw;
  background: var(--surf);
  border: 1px solid var(--tone);
  font: var(--fw-semibold) max(7px, 1.1cqw) var(--font-mono);
  color: var(--mut);
  transition:
    background 0.3s,
    color 0.3s;
}
.sector-chip.active {
  background: var(--tone);
  color: var(--white);
}

.route-tag {
  padding: 0.3cqw 1cqw;
  border-radius: 2cqw;
  background: var(--surf);
  border: 1px solid var(--line);
  font: var(--fw-semibold) max(7px, 1.1cqw) var(--font-mono);
  color: var(--mut);
}
.route-tag.sel {
  border-color: var(--acc);
  color: var(--accDeep);
}
.route-tag.alt {
  color: var(--amb);
}

.alt-tag {
  transform: translateY(-50%);
  padding: 0.3cqw 0.8cqw;
  border-radius: 0.6cqw;
  background: var(--ink);
  color: var(--surf);
  font: var(--fw-semibold) max(7px, 1.05cqw) var(--font-mono);
}

.incident-tag {
  font: var(--fw-semibold) max(7px, 1.1cqw) var(--font-sans);
  color: var(--white);
  background: var(--red);
  padding: 0.35cqw 1cqw;
  border-radius: 2cqw;
}
</style>
