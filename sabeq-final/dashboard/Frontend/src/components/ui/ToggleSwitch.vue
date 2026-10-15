<script setup>
// On/off switch visual.
defineProps({
  on: { type: Boolean, default: false },
})
</script>

<template>
  <span class="track" :class="{ on }" aria-hidden="true"><span class="knob" /></span>
</template>

<style scoped>
.track {
  width: 38px;
  height: 22px;
  border-radius: 11px;
  background: var(--line);
  position: relative;
  flex: none;
  transition: background 0.2s;
}

.knob {
  position: absolute;
  top: 3px;
  left: 3px;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: var(--white);
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.25);
  transition: left 0.2s;
}

.on {
  background: var(--acc);
}

.on .knob {
  left: 19px;
}
</style>
