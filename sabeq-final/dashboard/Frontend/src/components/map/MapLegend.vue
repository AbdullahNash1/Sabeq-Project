<script setup>
// Legend box in the bottom corner of the map.
import { useI18n } from '@/composables/useI18n'
import DroneMarker from '@/components/ui/DroneMarker.vue'

const { t } = useI18n()
</script>

<template>
  <div class="legend">
    <span class="item"><span class="sw incident" />{{ t.lIncident }}</span>
    <span class="item"><DroneMarker />{{ t.lDrone }}</span>
    <span class="item"><span class="sw cart" />{{ t.lCart }}</span>
    <span class="item"><span class="sw rec" />{{ t.lRec }}</span>
    <span class="item"><span class="sw alt" />{{ t.lAlt }}</span>
    <span class="item"><span class="sw aid">+</span>{{ t.firstAid }}</span>
  </div>
</template>

<style scoped>
.legend {
  position: absolute;
  bottom: var(--sp-7);
  inset-inline-start: var(--sp-7);
  display: flex;
  flex-wrap: wrap;
  gap: var(--sp-7);
  align-items: center;
  padding: 9px var(--sp-7);
  background: var(--surf);
  border: 1px solid var(--line);
  border-radius: var(--r-lg);
  box-shadow: var(--shadow);
  font-size: var(--fs-sm);
  color: var(--mut);
  max-width: calc(100% - 120px);
}

.item {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
}

.sw {
  flex: none;
}

.incident {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: var(--red);
}

.cart {
  width: 12px;
  height: 9px;
  border-radius: 3px;
  background: var(--red);
}

.rec {
  width: 18px;
  height: 4px;
  border-radius: 2px;
  background: var(--acc);
}

.alt {
  width: 18px;
  height: 0;
  border-top: 3px dashed var(--amb);
}

.aid {
  width: 12px;
  height: 12px;
  border-radius: 50%;
  border: 1.5px solid var(--red);
  box-sizing: border-box;
  display: grid;
  place-items: center;
  color: var(--red);
  font: 700 10px/1 sans-serif;
}
</style>
