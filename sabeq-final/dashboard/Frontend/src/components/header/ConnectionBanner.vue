<script setup>
// Slim banner while the WebSocket is down; the UI shows the last known state meanwhile.
import { useI18n } from '@/composables/useI18n'
import { useDashboardData } from '@/composables/useDashboardData'

const { t } = useI18n()
const { connection } = useDashboardData()
</script>

<template>
  <div v-if="connection !== 'open'" class="banner" role="status">
    <span class="dot" />{{ t.reconnecting }}
  </div>
</template>

<style scoped>
.banner {
  position: fixed;
  top: calc(var(--header-h) + var(--sp-4));
  left: 50%;
  transform: translateX(-50%);
  z-index: 20;
  display: flex;
  align-items: center;
  gap: var(--sp-4);
  padding: var(--sp-3) var(--sp-7);
  border-radius: var(--r-lg);
  background: var(--ambSoft);
  border: 1px solid var(--amb);
  color: var(--ink);
  font-size: var(--fs-md);
  font-weight: var(--fw-medium);
}

.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--amb);
  animation: sbqBlink 1.2s ease-in-out infinite;
}
</style>
