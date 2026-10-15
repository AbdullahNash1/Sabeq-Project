<script setup>
// Pill-shaped group of buttons where one is selected (used for language, theme, stages).
defineProps({
  modelValue: { type: String, required: true },
  options: { type: Array, required: true }, // [{ value, label }]
  vertical: { type: Boolean, default: false },
})
defineEmits(['update:modelValue'])
</script>

<template>
  <div class="segmented" :class="{ vertical }">
    <button
      v-for="opt in options"
      :key="opt.value"
      type="button"
      class="seg"
      :class="{ active: opt.value === modelValue }"
      :aria-pressed="opt.value === modelValue"
      @click="$emit('update:modelValue', opt.value)"
    >
      {{ opt.label }}
    </button>
  </div>
</template>

<style scoped>
.segmented {
  display: flex;
  gap: var(--sp-1);
  padding: 3px;
  background: var(--surf2);
  border: 1px solid var(--line);
  border-radius: var(--r-md);
}

.seg {
  border: 0;
  background: transparent;
  color: var(--mut);
  box-shadow: none;
  padding: 6px 11px;
  border-radius: var(--r-sm);
  font-size: 12.5px;
  font-weight: var(--fw-medium);
  cursor: pointer;
  white-space: nowrap;
}

.seg.active {
  background: var(--surf);
  color: var(--ink);
  box-shadow: var(--shadow);
}

.vertical {
  flex-direction: column;
  width: 62px;
  box-sizing: border-box;
}

.vertical .seg {
  padding: 6px 2px;
  font-size: var(--fs-xs);
}
</style>
