<script setup>
// Static site geometry: the Gazebo world (data/world.json, exported from gen_world.py by
// tools/export_world.py), projected with API_CONTRACT §2. Rendered inside the LiveMap <svg>.
// Defines #sbqSite (clip for the scan sectors). Routes, incident, drone and cart are drawn
// by MissionOverlay from backend data.
import { siteMap as m } from '@/data/siteMap'

const cross = (c, size, bar) => [
  { x: c[0] - size / 2, y: c[1] - bar / 2, width: size, height: bar },
  { x: c[0] - bar / 2, y: c[1] - size / 2, width: bar, height: size },
]
const aidCross = cross(m.firstAid.center, 22, 7)
</script>

<template>
  <g class="site-map-base">
    <defs>
      <clipPath id="sbqSite"><rect v-bind="m.site" rx="14"></rect></clipPath>
    </defs>

    <!-- Site (perimeter wall) -->
    <rect v-bind="m.site" rx="14" class="site" />

    <g clip-path="url(#sbqSite)">
      <!-- Gardens between the avenues, with their paved rest areas -->
      <polygon v-for="(g, i) in m.gardens" :key="'g' + i" :points="g" class="garden" />
      <template v-for="(p, i) in m.plots" :key="'p' + i">
        <rect v-if="p.kind === 'rect'" :x="p.x" :y="p.y" :width="p.width" :height="p.height" rx="3" class="plot" />
        <circle v-else :cx="p.cx" :cy="p.cy" :r="p.r" class="plot" />
      </template>

      <!-- Avenues: sidewalks, then the 8 m road the cart drives on -->
      <polyline
        v-for="(a, i) in m.avenues"
        :key="'w' + i"
        :points="a"
        class="walkway"
        :style="{ strokeWidth: m.walkwayWidth }"
      />
      <circle v-for="(c, i) in m.corners" :key="'c' + i" v-bind="c" class="walkway-corner" />
      <polyline
        v-for="(a, i) in m.avenues"
        :key="'r' + i"
        :points="a"
        class="road"
        :style="{ strokeWidth: m.roadWidth }"
      />

      <!-- Start plaza and Incident Plaza -->
      <circle v-for="p in m.plazas" :key="p.id" :cx="p.cx" :cy="p.cy" :r="p.r" class="plaza" />

      <!-- Fountains -->
      <g v-for="(f, i) in m.fountains" :key="'f' + i">
        <circle v-bind="f.rim" class="fountain-rim" />
        <circle v-bind="f.pool" class="water" />
      </g>

      <!-- Shade canopies (shadow, roof, roof edge) -->
      <g v-for="(c, i) in m.canopies" :key="'cn' + i">
        <rect :x="c.x" :y="c.y + 3" :width="c.width" :height="c.height" rx="4" class="shadow" />
        <rect v-bind="c" rx="4" class="canopy" />
        <rect :x="c.x + 5" :y="c.y + 5" :width="c.width - 10" :height="c.height - 10" rx="2" class="roof-edge" />
      </g>

      <!-- Food kiosks -->
      <polygon v-for="(k, i) in m.kiosks" :key="'k' + i" :points="k" class="kiosk" />

      <!-- First Aid Center: building, door (west side), cart stop -->
      <g class="first-aid">
        <rect
          :x="m.firstAid.building.x"
          :y="m.firstAid.building.y + 3"
          :width="m.firstAid.building.width"
          :height="m.firstAid.building.height"
          rx="4"
          class="shadow"
        />
        <rect v-bind="m.firstAid.building" rx="4" class="aid-building" />
        <rect
          :x="m.firstAid.building.x + 5"
          :y="m.firstAid.building.y + 5"
          :width="m.firstAid.building.width - 10"
          :height="m.firstAid.building.height - 10"
          rx="2"
          class="roof-edge"
        />
        <rect v-for="(b, i) in aidCross" :key="'x' + i" v-bind="b" rx="1.5" class="aid-cross" />
        <rect :x="m.firstAid.door[0] - 2.5" :y="m.firstAid.door[1] - 6" width="5" height="12" rx="1.5" class="aid-door" />
        <circle :cx="m.firstAid.cartStop[0]" :cy="m.firstAid.cartStop[1]" r="5" class="cart-stop" />
      </g>
    </g>
  </g>
</template>

<style scoped>
.site {
  fill: var(--ground);
  stroke: var(--line);
  stroke-width: 2;
}

.garden {
  fill: var(--park);
}

.plot {
  fill: var(--zA);
}

.walkway,
.road {
  fill: none;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.walkway {
  stroke: var(--plaza);
}

.walkway-corner {
  fill: var(--plaza);
}

.road {
  stroke: var(--path);
}

.plaza {
  fill: var(--plaza);
  stroke: var(--pavSh);
  stroke-width: 1.5;
}

.fountain-rim {
  fill: var(--zB);
}

.water {
  fill: var(--water);
}

.shadow {
  fill: var(--pavSh);
}

.canopy {
  fill: var(--zB);
}

.roof-edge {
  fill: none;
  stroke: var(--roof);
  stroke-width: 1.5;
}

.kiosk {
  fill: var(--zC);
  stroke: var(--pavSh);
  stroke-width: 1;
}

.aid-building {
  fill: var(--zA);
}

.aid-cross {
  fill: var(--red);
}

.aid-door {
  fill: var(--red);
}

.cart-stop {
  fill: none;
  stroke: var(--red);
  stroke-width: 1.5;
  stroke-dasharray: 3 3;
}
</style>
