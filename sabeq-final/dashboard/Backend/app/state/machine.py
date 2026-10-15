"""Mission state machine (API_CONTRACT.md §6).

Pure Python: no FastAPI, Pydantic or ROS imports, so it can be tested and
reused anywhere. The service layer calls `transition()` / `ensure_command_allowed()`
and performs the side effects listed in the contract itself.
"""
from enum import Enum


class MissionState(str, Enum):
    IDLE = "idle"
    INCIDENT_RECEIVED = "incident_received"
    TAKING_OFF = "taking_off"
    SCANNING = "scanning"
    ROUTE_SELECTED = "route_selected"
    ESCORTING = "escorting"
    ARRIVED = "arrived"
    RETURNING = "returning"
    COMPLETED = "completed"
    ABORTED = "aborted"
    ERROR = "error"


class Trigger(str, Enum):
    """What caused a transition (§6.1 'Trigger' column)."""

    INCIDENT_ACTIVATED = "incident_activated"  # POST /incidents while idle, or queue pop on close
    DISMISS = "dismiss"  # POST dismiss
    LAUNCH = "launch"  # POST launch
    SCAN_ALTITUDE_REACHED = "scan_altitude_reached"  # ROS /mission_events
    ROUTE_RECOMMENDED = "route_recommended"  # ROS /selected_route
    ROUTE_LOCKED = "route_locked"  # 3 s countdown expired, or POST route (override)
    CART_AT_INCIDENT = "cart_at_incident"  # ROS /cart/status at_incident
    START_RETURN = "start_return"  # POST return
    DRONE_LANDED = "drone_landed"  # ROS /mission_events landed_on_dock (after the cart is at the hub)
    ABORT = "abort"  # POST abort, or ROS battery_low
    FAULT = "fault"  # drone fault, link lost, node_error, command nack/timeout
    CLOSE = "close"  # POST close


class Command(str, Enum):
    """Operator commands from REST §4.4."""

    LAUNCH = "launch"
    DISMISS = "dismiss"
    ROUTE = "route"
    PAYLOAD = "payload"
    ABORT = "abort"
    RETURN = "return"
    CLOSE = "close"


S = MissionState

# States from which the operator (or low battery) can abort.
ABORTABLE_STATES = frozenset(
    {S.TAKING_OFF, S.SCANNING, S.ROUTE_SELECTED, S.ESCORTING, S.RETURNING}
)
# Terminal mission states, closed back to idle with POST close.
CLOSABLE_STATES = frozenset({S.COMPLETED, S.ABORTED, S.ERROR})
# "any non-idle" state can fault into error (error itself excluded: no self-loop).
FAULTABLE_STATES = frozenset(s for s in S if s not in (S.IDLE, S.ERROR))

# trigger -> (allowed source states, target state)
TRANSITIONS: dict = {
    Trigger.INCIDENT_ACTIVATED: (frozenset({S.IDLE}), S.INCIDENT_RECEIVED),
    Trigger.DISMISS: (frozenset({S.INCIDENT_RECEIVED}), S.IDLE),
    Trigger.LAUNCH: (frozenset({S.INCIDENT_RECEIVED}), S.TAKING_OFF),
    Trigger.SCAN_ALTITUDE_REACHED: (frozenset({S.TAKING_OFF}), S.SCANNING),
    Trigger.ROUTE_RECOMMENDED: (frozenset({S.SCANNING}), S.ROUTE_SELECTED),
    Trigger.ROUTE_LOCKED: (frozenset({S.ROUTE_SELECTED}), S.ESCORTING),
    Trigger.CART_AT_INCIDENT: (frozenset({S.ESCORTING}), S.ARRIVED),
    Trigger.START_RETURN: (frozenset({S.ARRIVED}), S.RETURNING),
    Trigger.DRONE_LANDED: (frozenset({S.RETURNING}), S.COMPLETED),
    Trigger.ABORT: (ABORTABLE_STATES, S.ABORTED),
    Trigger.FAULT: (FAULTABLE_STATES, S.ERROR),
    Trigger.CLOSE: (CLOSABLE_STATES, S.IDLE),
}

# Every (from, to) pair the machine can produce.
ALLOWED_PAIRS = frozenset(
    (src, dst) for sources, dst in TRANSITIONS.values() for src in sources
)

# §4.4 "Allowed in state" column. `route` and `payload` may not change state.
COMMAND_STATES: dict = {
    Command.LAUNCH: frozenset({S.INCIDENT_RECEIVED}),
    Command.DISMISS: frozenset({S.INCIDENT_RECEIVED}),
    Command.ROUTE: frozenset({S.SCANNING, S.ROUTE_SELECTED}),  # scanning = pre-select
    Command.PAYLOAD: frozenset(
        {S.TAKING_OFF, S.SCANNING, S.ROUTE_SELECTED, S.ESCORTING, S.ARRIVED, S.RETURNING}
    ),
    Command.ABORT: ABORTABLE_STATES,
    Command.RETURN: frozenset({S.ARRIVED}),
    Command.CLOSE: CLOSABLE_STATES,
}

CLOSE_OUTCOMES_FROM_COMPLETED = frozenset({"resolved", "transferred"})


class InvalidTransition(Exception):
    """A trigger or command is not allowed in the current state (HTTP 409)."""

    def __init__(self, state: MissionState, action: str):
        self.state = MissionState(state)
        self.action = action
        super().__init__(f"Cannot {action} from state '{self.state.value}'")


class InvalidOutcome(ValueError):
    """POST close with a missing or wrong outcome for the state (HTTP 400)."""


def can_transition(state: MissionState, trigger: Trigger) -> bool:
    sources, _ = TRANSITIONS[Trigger(trigger)]
    return MissionState(state) in sources


def transition(state: MissionState, trigger: Trigger) -> MissionState:
    """Return the next state, or raise InvalidTransition."""
    state, trigger = MissionState(state), Trigger(trigger)
    sources, target = TRANSITIONS[trigger]
    if state not in sources:
        raise InvalidTransition(state, trigger.value)
    return target


def ensure_command_allowed(state: MissionState, command: Command) -> None:
    """Raise InvalidTransition if the REST command is not allowed in `state`."""
    state, command = MissionState(state), Command(command)
    if state not in COMMAND_STATES[command]:
        raise InvalidTransition(state, command.value)


def resolve_close_outcome(state: MissionState, requested: "str | None") -> str:
    """Outcome recorded by POST close (§4.4, §6.1).

    completed -> requested must be resolved | transferred
    aborted   -> always "aborted" (requested is ignored)
    error     -> always "error"   (requested is ignored)
    """
    state = MissionState(state)
    ensure_command_allowed(state, Command.CLOSE)
    if state is S.ABORTED:
        return "aborted"
    if state is S.ERROR:
        return "error"
    if requested not in CLOSE_OUTCOMES_FROM_COMPLETED:
        raise InvalidOutcome(
            "outcome must be 'resolved' or 'transferred' when closing a completed mission"
        )
    return requested
