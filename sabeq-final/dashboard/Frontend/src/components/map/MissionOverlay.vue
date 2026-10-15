<script setup>
// Moving parts drawn on top of the map: routes, scan sectors, incident pulse, cart and drone.
import { computed } from 'vue'
import { useMissionView } from '@/composables/useMissionView'
import { useDashboardData } from '@/composables/useDashboardData'
import { toTranslate } from '@/utils/geometry'

const v = useMissionView()
const { drone } = useDashboardData()

const toPoints = (pts) => pts.map((p) => p.join(',')).join(' ')
const routes = computed(() =>
  v.routeLines.value.map((r) => ({ id: r.id, points: toPoints(r.points) })),
)
const travelPoints = computed(() => toPoints(v.travelPts.value))
// CSS transform (not the SVG attribute) so telemetry steps glide instead of jumping.
// CSS needs units; px are SVG user units here.
const moveStyle = (p) => ({ transform: `translate(${p[0].toFixed(1)}px, ${p[1].toFixed(1)}px)` })
</script>

<template>
  <g class="mission-overlay">
    <!-- Candidate routes: white casing under the selected one, then the line -->
    <template v-if="v.hasMission.value">
      <polyline
        v-for="r in routes"
        :key="'case-' + r.id"
        :points="r.points"
        class="route casing"
        :class="{ on: v.routeVariants.value[r.id] === 'sel' }"
      />
      <polyline
        v-for="r in routes"
        :key="'line-' + r.id"
        :points="r.points"
        class="route"
        :class="v.routeVariants.value[r.id]"
      />
      <polyline
        v-if="v.onRoute.value && v.travelPts.value.length"
        :points="travelPoints"
        class="route travel"
        :style="{ strokeDasharray: v.travelDash.value }"
      />
    </template>

    <!-- Scan: one sector per route from the drone above the cart; the faced one is lit -->
    <g v-if="v.isScan.value" clip-path="url(#sbqSite)" class="no-events">
      <path
        v-for="s in v.sectors.value"
        :key="s.id"
        :d="s.path"
        class="sector"
        :class="{ active: s.active }"
        :style="{ '--tone': `var(--${s.tone})` }"
      />
    </g>

    <!-- Incident marker -->
    <g v-if="v.incidentPoint.value" :transform="toTranslate(v.incidentPoint.value)">
      <circle r="10" class="incident-pulse" />
      <circle r="10" class="incident-dot" />
    </g>

    <!-- EMS cart -->
    <g class="mover" :style="moveStyle(v.positions.value.cart)">
      <rect x="-12" y="-9" width="24" height="18" rx="5" class="cart-body" />
      <rect x="-1.5" y="-5" width="3" height="10" class="cart-cross" />
      <rect x="-5" y="-1.5" width="10" height="3" class="cart-cross" />
    </g>

    <!-- Drone -->
    <g class="mover" :style="moveStyle(v.positions.value.drone)">
      <circle v-if="v.showRings.value" r="12" class="drone-ring" />
      <circle :r="v.haloR.value" class="drone-halo" />
      <circle r="12" class="drone-body" />
      <circle r="4" class="drone-core" />
      <rect x="-28" y="-40" width="56" height="18" rx="9" class="drone-tag" />
      <text y="-27.5" class="drone-tag-text">{{ drone.id }}</text>
    </g>
  </g>
</template>

<style scoped>
.route {
  fill: none;
  stroke-linecap: round;
  stroke-linejoin: round;
  transition:
    stroke 0.3s,
    opacity 0.3s;
}

.casing {
  stroke: var(--surf);
  stroke-width: 0;
}
.casing.on {
  stroke-width: 12;
}

.faint {
  stroke: var(--mut);
  stroke-width: 3;
  stroke-dasharray: 6 9;
  opacity: 0.55;
}

.sel {
  stroke: var(--acc);
  stroke-width: 6;
}

.alt {
  stroke: var(--amb);
  stroke-width: 4;
  stroke-dasharray: 8 8;
}

.pend {
  stroke: var(--ink);
  stroke-width: 3;
  stroke-dasharray: 4 8;
  opacity: 0.55;
}

.travel {
  stroke: var(--accDeep);
  stroke-width: 6;
}

.mover {
  transition: transform 0.2s linear;
}

.no-events {
  pointer-events: none;
}

.sector {
  fill: var(--tone);
  fill-opacity: 0.1;
  stroke: var(--tone);
  stroke-width: 1.5;
  stroke-opacity: 0.45;
  stroke-dasharray: 5 5;
  transition:
    fill-opacity 0.3s,
    stroke-opacity 0.3s;
}

.sector.active {
  fill-opacity: 0.32;
  stroke-opacity: 1;
  stroke-dasharray: none;
  stroke-width: 2.5;
}

.incident-pulse {
  fill: var(--red);
  transform-box: fill-box;
  transform-origin: center;
  animation: sbqPulse 1.8s ease-out infinite;
}

.incident-dot {
  fill: var(--red);
  stroke: var(--surf);
  stroke-width: 3;
}

.cart-body {
  fill: var(--red);
  stroke: var(--surf);
  stroke-width: 2.5;
}

.cart-cross {
  fill: var(--white);
}

.drone-ring {
  fill: none;
  stroke: var(--acc);
  stroke-width: 2;
  transform-box: fill-box;
  transform-origin: center;
  animation: sbqPulse 1.4s ease-out infinite;
}

.drone-halo {
  fill: var(--acc);
  fill-opacity: 0.16;
}

.drone-body {
  fill: var(--surf);
  stroke: var(--acc);
  stroke-width: 3;
}

.drone-core {
  fill: var(--acc);
}

.drone-tag {
  fill: var(--ink);
}

.drone-tag-text {
  font: var(--fw-semibold) 10px var(--font-mono);
  fill: var(--surf);
  text-anchor: middle;
}
</style>
