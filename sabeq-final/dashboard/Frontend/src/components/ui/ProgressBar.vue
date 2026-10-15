<script setup>
// Horizontal progress bar (0–100).
import { computed } from 'vue'

const props = defineProps({
  value: { type: Number, required: true }, // 0–100
  height: { type: Number, default: 6 },
  color: { type: String, default: 'var(--acc)' },
  track: { type: String, default: 'var(--surf2)' },
})

const radius = computed(() => Math.ceil(props.height / 2) + 'px')
</script>

<template>
  <div
    class="track"
    role="progressbar"
    :aria-valuenow="Math.round(value)"
    aria-valuemin="0"
    aria-valuemax="100"
    :style="{ height: height + 'px', borderRadius: radius, background: track }"
  >
    <div
      class="fill"
      :style="{ width: value.toFixed(1) + '%', background: color, borderRadius: radius }"
    />
  </div>
</template>

<style scoped>
.track {
  overflow: hidden;
}

.fill {
  height: 100%;
}
</style>
