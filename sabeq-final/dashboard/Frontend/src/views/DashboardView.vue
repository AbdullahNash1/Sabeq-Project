<script setup>
// The dashboard page. Connects to the backend and places the screen areas in a grid.
import AppHeader from '@/components/header/AppHeader.vue'
import DevNav from '@/components/stage-nav/DevNav.vue'
import LiveMap from '@/components/map/LiveMap.vue'
import AlertsPanel from '@/components/alerts/AlertsPanel.vue'
import ConnectionBanner from '@/components/header/ConnectionBanner.vue'
import Notices from '@/components/ui/Notices.vue'
import { useI18n } from '@/composables/useI18n'
import { useDashboardData } from '@/composables/useDashboardData'
import { connectSocket } from '@/api/socket'

const { t, dir } = useI18n()
const { ready } = useDashboardData()
// The nav column only holds dev controls (backend DEV_MODE must be on too)
const devMode = import.meta.env.VITE_DEV_MODE === 'true'

connectSocket()
</script>

<template>
  <div v-if="ready" class="dashboard" :class="{ 'no-nav': !devMode }" :dir="dir">
    <AppHeader class="area-header" />
    <DevNav v-if="devMode" class="area-nav" />
    <main class="area-main">
      <LiveMap />
    </main>
    <AlertsPanel class="area-aside" />
    <ConnectionBanner />
  </div>
  <div v-else class="connecting" :dir="dir"><span class="dot" />{{ t.connecting }}</div>
  <Notices :dir="dir" />
</template>

<style scoped>
.dashboard {
  display: grid;
  grid-template-columns: var(--nav-w) minmax(0, 1fr) var(--aside-w);
  grid-template-rows: var(--header-h) minmax(0, 1fr);
  grid-template-areas:
    'header header header'
    'nav main aside';
  height: 100vh;
  background: var(--bg);
  color: var(--ink);
  transition: background 0.25s;
}

.dashboard.no-nav {
  grid-template-columns: minmax(0, 1fr) var(--aside-w);
  grid-template-areas:
    'header header'
    'main aside';
}

.connecting {
  height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--sp-5);
  background: var(--bg);
  color: var(--mut);
  font-size: var(--fs-md);
}
.connecting .dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: var(--amb);
  animation: sbqBlink 1.2s ease-in-out infinite;
}

.area-header {
  grid-area: header;
}
.area-nav {
  grid-area: nav;
}
.area-aside {
  grid-area: aside;
}

.area-main {
  grid-area: main;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

/* Tablet: alerts move under the map, page scrolls */
@media (max-width: 1179px) {
  .dashboard {
    grid-template-columns: var(--nav-w) minmax(0, 1fr);
    grid-template-rows: auto auto auto;
    grid-template-areas:
      'header header'
      'nav main'
      'nav aside';
    height: auto;
    min-height: 100vh;
  }
  .dashboard.no-nav {
    grid-template-columns: minmax(0, 1fr);
    grid-template-areas:
      'header'
      'main'
      'aside';
  }
  .area-aside {
    border-inline-start: 0;
    border-top: 1px solid var(--line);
    overflow: visible;
  }
}

/* Phone: single column, stage switcher becomes a horizontal strip */
@media (max-width: 767px) {
  .dashboard {
    grid-template-columns: minmax(0, 1fr);
    grid-template-areas:
      'header'
      'nav'
      'main'
      'aside';
  }
}
</style>
