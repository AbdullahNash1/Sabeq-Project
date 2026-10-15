<script setup>
// Narrow left column with dev controls (VITE_DEV_MODE=true). The backend must run with DEV_MODE=true.
import AppButton from '@/components/ui/AppButton.vue'
import { useI18n } from '@/composables/useI18n'
import { useMission } from '@/composables/useMission'

const { t } = useI18n()
const { devReset, pending } = useMission()
</script>

<template>
  <nav class="stage-nav">
    <span class="title">{{ t.dev }}</span>
    <AppButton variant="quiet" class="reset" :disabled="!!pending" @click="devReset">
      {{ t.resetSim }}
    </AppButton>
  </nav>
</template>

<style scoped>
.stage-nav {
  background: var(--surf);
  border-inline-end: 1px solid var(--line);
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--sp-4);
  padding: var(--sp-8) var(--sp-3);
}

.title {
  font: var(--fw-medium) var(--fs-2xs) var(--font-mono);
  color: var(--mut);
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.reset {
  width: 100%;
  font-size: var(--fs-xs);
  padding: var(--sp-4) var(--sp-2);
}

@media (max-width: 767px) {
  .stage-nav {
    flex-direction: row;
    border-inline-end: 0;
    border-bottom: 1px solid var(--line);
    padding: var(--sp-4) var(--sp-8);
  }
  .reset {
    width: auto;
  }
}
</style>
