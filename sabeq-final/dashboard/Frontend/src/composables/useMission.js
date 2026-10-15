// THE MISSION STATE: the backend's active mission mapped to UI stages, plus the
// operator commands. Commands go over REST only; state comes back over the WebSocket.
import { computed, ref } from 'vue'
import { dashboardApi } from '@/api/dashboardApi'
import { useDashboardData } from './useDashboardData'
import { notifyError } from './useNotices'

// Backend mission state -> UI stage (API_CONTRACT §6.2)
const STAGE_OF = {
  idle: 'idle',
  incident_received: 'alert',
  taking_off: 'scan',
  scanning: 'scan',
  route_selected: 'scan',
  escorting: 'escort',
  arrived: 'arrived',
  returning: 'returning',
  completed: 'completed',
  aborted: 'aborted',
  error: 'error',
}

// Name of the command in flight (buttons are disabled meanwhile)
const pending = ref(null)

async function run(name, call) {
  if (pending.value) return
  pending.value = name
  try {
    await call()
  } catch (err) {
    notifyError(err.code, err.message)
  } finally {
    pending.value = null
  }
}

export function useMission() {
  const { mission } = useDashboardData()
  const state = computed(() => mission.value?.state ?? 'idle')
  const stage = computed(() => STAGE_OF[state.value] ?? 'idle')
  const id = () => mission.value.id

  return {
    mission,
    state,
    stage,
    pending,
    launch: () => run('launch', () => dashboardApi.launch(id())),
    dismiss: () => run('dismiss', () => dashboardApi.dismiss(id())),
    selectRoute: (route) => run('route', () => dashboardApi.selectRoute(id(), route)),
    toggleSpeaker: () =>
      run('payload', () =>
        dashboardApi.setPayload(id(), { speaker: !mission.value.payload.speaker }),
      ),
    toggleLed: () =>
      run('payload', () => dashboardApi.setPayload(id(), { led: !mission.value.payload.led })),
    abort: (reason = 'operator_abort') => run('abort', () => dashboardApi.abort(id(), reason)),
    startReturn: () => run('return', () => dashboardApi.startReturn(id())),
    close: (outcome) => run('close', () => dashboardApi.close(id(), outcome)),
    devReset: () => run('reset', () => dashboardApi.devReset()),
  }
}
