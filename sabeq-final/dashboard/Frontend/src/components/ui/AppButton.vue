<script setup>
// Generic button with colour variants (primary, secondary, outline, quiet, danger).
defineProps({
  // primary | secondary | outline | quiet | danger
  variant: { type: String, default: 'secondary' },
})
</script>

<template>
  <button type="button" class="btn" :class="variant">
    <slot />
  </button>
</template>

<style scoped>
.btn {
  cursor: pointer;
  font-size: var(--fs-md);
  border-radius: var(--r-md);
  padding: 11px;
}

.btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
  filter: none;
}

.primary {
  border: 0;
  border-radius: var(--r-lg);
  background: var(--btn);
  color: var(--btnInk);
  padding: 13px;
  font-size: var(--fs-base);
  font-weight: var(--fw-semibold);
}
.primary:hover {
  filter: brightness(1.08);
}

.secondary {
  border: 1px solid var(--line);
  background: var(--surf);
  color: var(--ink);
  font-weight: var(--fw-medium);
}
.secondary:hover {
  background: var(--surf2);
}

.outline {
  border: 1px solid var(--line);
  background: transparent;
  color: var(--ink);
  padding: var(--sp-5);
}
.outline:hover {
  background: var(--surf2);
}

.quiet {
  border: 1px solid var(--line);
  background: transparent;
  color: var(--mut);
  padding: 9px;
}
.quiet:hover {
  color: var(--ink);
  background: var(--surf);
}

.danger {
  border: 1.5px solid var(--red);
  background: transparent;
  color: var(--red);
  font-weight: var(--fw-semibold);
}
.danger:hover {
  background: var(--redSoft);
}
</style>
