<script setup>
// One small status chip in the header, e.g. "● ROS 2 OK".
defineProps({
  label: { type: String, required: true },
  value: { type: String, required: true },
  ok: { type: Boolean, default: true },
})
</script>

<template>
  <div class="beat">
    <span class="dot" :class="{ bad: !ok }" />
    <span class="label">{{ label }}</span>
    <span>{{ value }}</span>
  </div>
</template>

<style scoped>
.beat {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  padding: 5px 9px;
  border: 1px solid var(--line);
  border-radius: 8px;
  font: var(--fw-medium) 11.5px var(--font-mono);
  color: var(--mut);
  white-space: nowrap;
}

.dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--acc);
  animation: sbqBlink 2s ease-in-out infinite;
}

.dot.bad {
  background: var(--red);
  animation: none;
}

.label {
  color: var(--ink);
}
</style>
