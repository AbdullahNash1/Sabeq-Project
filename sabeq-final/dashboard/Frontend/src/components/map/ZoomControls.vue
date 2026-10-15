<script setup>
// The + / − zoom buttons on the map.
defineEmits(['zoom-in', 'zoom-out'])
</script>

<template>
  <div class="zoom">
    <button type="button" aria-label="Zoom in" @click="$emit('zoom-in')">+</button>
    <div class="sep" />
    <button type="button" aria-label="Zoom out" @click="$emit('zoom-out')">−</button>
  </div>
</template>

<style scoped>
.zoom {
  position: absolute;
  bottom: var(--sp-7);
  inset-inline-end: var(--sp-7);
  display: flex;
  flex-direction: column;
  background: var(--surf);
  border: 1px solid var(--line);
  border-radius: var(--r-md);
  overflow: hidden;
  box-shadow: var(--shadow);
}

button {
  width: 36px;
  height: 36px;
  border: 0;
  background: transparent;
  color: var(--ink);
  font-size: var(--fs-3xl);
  cursor: pointer;
}

button:hover {
  background: var(--surf2);
}

.sep {
  height: 1px;
  background: var(--line);
}
</style>
