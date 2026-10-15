<script setup>
// Stage "Idle": all-clear banner, drone status and recent incidents.
import DroneStatusCard from '@/components/alerts/DroneStatusCard.vue'
import RecentList from '@/components/alerts/RecentList.vue'
import { useI18n } from '@/composables/useI18n'

const { t } = useI18n()
</script>

<template>
  <div class="contents">
    <div class="all-clear">
      <span class="dot" />
      <div class="text">
        <span class="title">{{ t.allClear }}</span>
        <span class="sub">{{ t.allClearSub }}</span>
      </div>
    </div>
    <DroneStatusCard />
    <RecentList />
  </div>
</template>

<style scoped>
.contents {
  display: contents;
}

.all-clear {
  padding: var(--sp-9);
  border-radius: var(--r-xl);
  background: var(--accSoft);
  display: flex;
  gap: var(--sp-7);
  align-items: flex-start;
}

.dot {
  width: 12px;
  height: 12px;
  border-radius: 50%;
  background: var(--acc);
  margin-top: 5px;
  flex: none;
  animation: sbqBlink 2.4s ease-in-out infinite;
}

.text {
  display: flex;
  flex-direction: column;
  gap: var(--sp-2);
}

.title {
  font-weight: var(--fw-semibold);
  font-size: var(--fs-lg);
}

.sub {
  font-size: var(--fs-md);
  color: var(--mut);
  text-wrap: pretty;
}
</style>
