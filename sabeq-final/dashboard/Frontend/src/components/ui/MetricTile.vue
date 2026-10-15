<script setup>
// Small box with a label and a big number, in three sizes.
defineProps({
  label: { type: String, required: true },
  value: { type: [String, Number], required: true },
  // sm: incident facts · md: live telemetry · lg: mission summary
  size: { type: String, default: 'md' },
  // Solid surface background (used on tinted cards)
  filled: { type: Boolean, default: false },
  // Optional value colour token: 'amb' | 'accDeep'
  tone: { type: String, default: '' },
})
</script>

<template>
  <div class="tile" :class="[size, { filled }]">
    <span class="label">{{ label }}</span>
    <span class="value" :class="tone && `tone-${tone}`">{{ value }}</span>
  </div>
</template>

<style scoped>
.tile {
  border: 1px solid var(--line);
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
}

.filled {
  background: var(--surf);
}

.label {
  font-size: var(--fs-xs);
  color: var(--mut);
}

.value {
  font-family: var(--font-mono);
  font-weight: var(--fw-semibold);
}

.sm {
  padding: var(--sp-5);
  border-radius: var(--r-md);
}
.sm .value {
  font-size: var(--fs-md);
}

.md {
  padding: var(--sp-5) var(--sp-6);
  border-radius: var(--r-md);
}
.md .value {
  font-size: var(--fs-xl);
}

.lg {
  padding: var(--sp-7);
  border-radius: var(--r-lg);
  gap: var(--sp-2);
}
.lg .label {
  font-size: var(--fs-sm);
}
.lg .value {
  font-size: var(--fs-4xl);
}

.tone-amb {
  color: var(--amb);
}
.tone-accDeep {
  color: var(--accDeep);
}
</style>
