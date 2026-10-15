<script setup>
// Error toasts for the operator (REST command failures, WS `error` messages).
import { useNotices } from '@/composables/useNotices'

const { notices, dismissNotice } = useNotices()
</script>

<template>
  <div class="notices" aria-live="assertive">
    <div v-for="n in notices" :key="n.id" class="notice" role="alert">
      <div class="text">
        <span class="title">{{ n.title }}</span>
        <span v-if="n.detail" class="detail">{{ n.detail }}</span>
      </div>
      <button type="button" class="x" aria-label="Dismiss" @click="dismissNotice(n.id)">×</button>
    </div>
  </div>
</template>

<style scoped>
.notices {
  position: fixed;
  bottom: var(--sp-8);
  inset-inline-end: var(--sp-8);
  z-index: 30;
  display: flex;
  flex-direction: column;
  gap: var(--sp-4);
  max-width: min(380px, calc(100vw - 32px));
}

.notice {
  display: flex;
  align-items: flex-start;
  gap: var(--sp-5);
  padding: var(--sp-6) var(--sp-7);
  border-radius: var(--r-lg);
  background: var(--surf);
  border: 1.5px solid var(--red);
  box-shadow: 0 6px 24px rgb(0 0 0 / 0.12);
  animation: sbqIn 0.25s ease-out;
}

.text {
  display: flex;
  flex-direction: column;
  gap: var(--sp-2);
  min-width: 0;
}

.title {
  color: var(--red);
  font-weight: var(--fw-semibold);
  font-size: var(--fs-md);
}

.detail {
  color: var(--mut);
  font-size: var(--fs-sm);
  overflow-wrap: anywhere;
}

.x {
  margin-inline-start: auto;
  border: 0;
  background: transparent;
  color: var(--mut);
  font-size: 18px;
  line-height: 1;
  cursor: pointer;
}
</style>
