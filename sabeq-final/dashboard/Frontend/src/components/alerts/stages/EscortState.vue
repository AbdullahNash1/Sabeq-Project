<script setup>
// Stages "Escort" and "Returning": elapsed timer, camera, drone/cart status, speaker/LED toggles.
import { computed } from 'vue'
import AppButton from '@/components/ui/AppButton.vue'
import ProgressBar from '@/components/ui/ProgressBar.vue'
import ToggleSwitch from '@/components/ui/ToggleSwitch.vue'
import DroneMarker from '@/components/ui/DroneMarker.vue'
import CameraFeed from '@/components/alerts/CameraFeed.vue'
import { useI18n } from '@/composables/useI18n'
import { useMission } from '@/composables/useMission'
import { useMissionView } from '@/composables/useMissionView'
import { useDashboardData } from '@/composables/useDashboardData'
import { fmtKmh } from '@/utils/format'

const { t } = useI18n()
const { mission, toggleSpeaker, toggleLed, abort, pending } = useMission()
const { escortElapsed, viaText, droneBatt, isReturning, cartAtAid } = useMissionView()
const { drone, cart } = useDashboardData()

const title = computed(() => (isReturning.value ? t.value.returningT : t.value.escort))
const caption = computed(() => (isReturning.value ? t.value.etaHub : t.value.etaPatient))
const ahead = computed(() => {
  const m = mission.value.escort.droneAheadOfCartM
  return m == null ? '—' : Math.max(0, Math.round(m))
})

const payloads = computed(() => [
  {
    key: 'speaker',
    label: t.value.speaker,
    sub: t.value.speakerSub,
    on: mission.value.payload.speaker,
    toggle: toggleSpeaker,
  },
  {
    key: 'led',
    label: t.value.led,
    sub: t.value.ledSub,
    on: mission.value.payload.led,
    toggle: toggleLed,
  },
])
</script>

<template>
  <div class="contents">
    <div class="timer">
      <span class="title">{{ title }}</span>
      <div class="timer-row">
        <span class="elapsed">{{ escortElapsed }}</span>
        <span class="caption">{{ caption }}<br />{{ viaText }}</span>
      </div>
      <ProgressBar :value="mission.escort.progress * 100" :height="6" />
      <div v-if="cartAtAid" class="landing" role="status">
        <span class="spinner" aria-hidden="true" />{{ t.droneLanding }}
      </div>
    </div>

    <CameraFeed />

    <div class="units">
      <div class="unit">
        <span class="unit-name"
          ><DroneMarker /><span class="mono strong">{{ drone.id }}</span></span
        >
        <span class="muted">{{ ahead }} {{ t.ahead }} · {{ droneBatt }}%</span>
      </div>
      <div class="unit">
        <span class="unit-name"
          ><span class="cart-sw" /><span class="medium">{{ t.cart }}</span></span
        >
        <span class="muted mono">{{ fmtKmh(cart.speedKmh) }}</span>
      </div>
    </div>

    <div class="payloads">
      <button
        v-for="pl in payloads"
        :key="pl.key"
        type="button"
        class="payload"
        :aria-pressed="pl.on"
        :disabled="!!pending"
        @click="pl.toggle"
      >
        <span class="payload-text">
          <span class="medium">{{ pl.label }}</span>
          <span class="payload-sub">{{ pl.sub }}</span>
        </span>
        <ToggleSwitch :on="pl.on" />
      </button>
    </div>

    <div class="actions">
      <AppButton variant="secondary" :disabled="!!pending" @click="abort('return_to_dock')">{{
        t.rth
      }}</AppButton>
      <AppButton variant="danger" :disabled="!!pending" @click="abort('operator_abort')">{{
        t.abort
      }}</AppButton>
    </div>
  </div>
</template>

<style scoped>
.contents {
  display: contents;
}

.timer {
  display: flex;
  flex-direction: column;
  gap: var(--sp-5);
}

.title {
  font-size: var(--fs-xl);
  font-weight: var(--fw-semibold);
}

.landing {
  display: flex;
  align-items: center;
  gap: var(--sp-4);
  padding: var(--sp-5) var(--sp-7);
  border-radius: var(--r-lg);
  background: var(--accSoft);
  font-weight: var(--fw-medium);
}
.spinner {
  width: 16px;
  height: 16px;
  flex: none;
  border-radius: 50%;
  border: 2.5px solid var(--line);
  border-top-color: var(--acc);
  animation: sbqSpin 0.9s linear infinite;
}
@keyframes sbqSpin {
  to {
    transform: rotate(360deg);
  }
}
.timer-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--sp-5);
}

.elapsed {
  font: var(--fw-semibold) var(--fs-hero) / 1 var(--font-mono);
  letter-spacing: -0.02em;
}

.caption {
  font-size: var(--fs-md);
  color: var(--mut);
  text-align: end;
}

.units {
  display: flex;
  flex-direction: column;
  border: 1px solid var(--line);
  border-radius: var(--r-lg);
  overflow: hidden;
  font-size: var(--fs-md);
}

.unit {
  display: flex;
  justify-content: space-between;
  gap: var(--sp-5);
  padding: 11px var(--sp-7);
}

.unit + .unit {
  border-top: 1px solid var(--line);
}

.unit-name {
  display: flex;
  align-items: center;
  gap: var(--sp-4);
}

.strong {
  font-weight: var(--fw-semibold);
}

.medium {
  font-weight: var(--fw-medium);
}

.cart-sw {
  width: 12px;
  height: 9px;
  border-radius: 3px;
  background: var(--red);
}

.payloads {
  display: flex;
  flex-direction: column;
  gap: var(--sp-5);
}

.payload {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--sp-5);
  padding: var(--sp-6) var(--sp-7);
  border: 1px solid var(--line);
  border-radius: var(--r-lg);
  background: var(--surf);
  color: var(--ink);
  cursor: pointer;
  font-size: var(--fs-md);
  text-align: start;
}

.payload-text {
  display: flex;
  flex-direction: column;
  gap: var(--sp-1);
}

.payload-sub {
  font-size: var(--fs-sm);
  color: var(--mut);
}

.actions {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--sp-4);
}
</style>
