<script setup>
// Small inline drone glyph (ring) used in legends and status rows
</script>

<template>
  <span class="drone-marker" aria-hidden="true" />
</template>

<style scoped>
.drone-marker {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  border: 2.5px solid var(--acc);
  box-sizing: border-box;
  flex: none;
}
</style>
