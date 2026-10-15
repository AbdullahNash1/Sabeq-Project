"""State machine vs API_CONTRACT.md §6.1 and §4.4.

The EXPECTED_* tables are written out by hand from the contract, independently of
app/state/machine.py, so a change to either side shows up as a failure.
"""
import itertools

import pytest

from app.state.machine import (
    ALLOWED_PAIRS,
    TRANSITIONS,
    Command,
    InvalidOutcome,
    InvalidTransition,
    MissionState as S,
    Trigger as T,
    can_transition,
    ensure_command_allowed,
    resolve_close_outcome,
    transition,
)

ACTIVE = [S.TAKING_OFF, S.SCANNING, S.ROUTE_SELECTED, S.ESCORTING, S.RETURNING]
NON_IDLE_EXCEPT_ERROR = [s for s in S if s not in (S.IDLE, S.ERROR)]

# (from, trigger) -> to, straight from §6.1
EXPECTED_TRANSITIONS = {
    (S.IDLE, T.INCIDENT_ACTIVATED): S.INCIDENT_RECEIVED,
    (S.INCIDENT_RECEIVED, T.DISMISS): S.IDLE,
    (S.INCIDENT_RECEIVED, T.LAUNCH): S.TAKING_OFF,
    (S.TAKING_OFF, T.SCAN_ALTITUDE_REACHED): S.SCANNING,
    (S.SCANNING, T.ROUTE_RECOMMENDED): S.ROUTE_SELECTED,
    (S.ROUTE_SELECTED, T.ROUTE_LOCKED): S.ESCORTING,
    (S.ESCORTING, T.CART_AT_INCIDENT): S.ARRIVED,
    (S.ARRIVED, T.START_RETURN): S.RETURNING,
    (S.RETURNING, T.DRONE_LANDED): S.COMPLETED,  # v1.4: the drone has landed on the cart
    **{(s, T.ABORT): S.ABORTED for s in ACTIVE},
    **{(s, T.FAULT): S.ERROR for s in NON_IDLE_EXCEPT_ERROR},
    (S.COMPLETED, T.CLOSE): S.IDLE,
    (S.ABORTED, T.CLOSE): S.IDLE,
    (S.ERROR, T.CLOSE): S.IDLE,
}

EXPECTED_PAIRS = {(src, dst) for (src, _), dst in EXPECTED_TRANSITIONS.items()}

# §4.4 "Allowed in state"
EXPECTED_COMMANDS = {
    Command.LAUNCH: {S.INCIDENT_RECEIVED},
    Command.DISMISS: {S.INCIDENT_RECEIVED},
    Command.ROUTE: {S.SCANNING, S.ROUTE_SELECTED},
    Command.PAYLOAD: {S.TAKING_OFF, S.SCANNING, S.ROUTE_SELECTED, S.ESCORTING, S.ARRIVED, S.RETURNING},
    Command.ABORT: set(ACTIVE),
    Command.RETURN: {S.ARRIVED},
    Command.CLOSE: {S.COMPLETED, S.ABORTED, S.ERROR},
}

ALL_STATE_TRIGGER = list(itertools.product(S, T))
REJECTED = [(s, t) for s, t in ALL_STATE_TRIGGER if (s, t) not in EXPECTED_TRANSITIONS]


def _id(pair):
    return f"{pair[0].value}--{pair[1].value}"


# --- transitions ------------------------------------------------------------


@pytest.mark.parametrize("pair", list(EXPECTED_TRANSITIONS), ids=_id)
def test_allowed_transition(pair):
    state, trigger = pair
    assert can_transition(state, trigger)
    assert transition(state, trigger) is EXPECTED_TRANSITIONS[pair]


@pytest.mark.parametrize("pair", REJECTED, ids=_id)
def test_rejected_transition(pair):
    state, trigger = pair
    assert not can_transition(state, trigger)
    with pytest.raises(InvalidTransition) as exc:
        transition(state, trigger)
    assert exc.value.state is state
    assert exc.value.action == trigger.value


def test_table_matches_contract_exactly():
    actual = {
        (src, trigger): dst
        for trigger, (sources, dst) in TRANSITIONS.items()
        for src in sources
    }
    assert actual == EXPECTED_TRANSITIONS


@pytest.mark.parametrize(
    "pair", list(itertools.product(S, S)), ids=lambda p: f"{p[0].value}->{p[1].value}"
)
def test_full_state_grid(pair):
    """Every (from, to) pair is reachable iff the contract allows it."""
    assert (pair in ALLOWED_PAIRS) == (pair in EXPECTED_PAIRS)


def test_every_state_and_trigger_is_covered():
    assert set(TRANSITIONS) == set(T)
    reachable = {dst for _, dst in EXPECTED_PAIRS} | {src for src, _ in EXPECTED_PAIRS}
    assert reachable == set(S)


def test_error_message_matches_contract_example():
    with pytest.raises(InvalidTransition, match="Cannot launch from state 'escorting'"):
        transition(S.ESCORTING, T.LAUNCH)


def test_accepts_plain_strings():
    assert transition("incident_received", "launch") is S.TAKING_OFF


# --- full paths -------------------------------------------------------------


def _run(path):
    state = S.IDLE
    for trigger in path:
        state = transition(state, trigger)
    return state


def test_happy_path_with_return_leg():
    path = [
        T.INCIDENT_ACTIVATED, T.LAUNCH, T.SCAN_ALTITUDE_REACHED, T.ROUTE_RECOMMENDED,
        T.ROUTE_LOCKED, T.CART_AT_INCIDENT, T.START_RETURN, T.DRONE_LANDED, T.CLOSE,
    ]
    assert _run(path) is S.IDLE


def test_dismiss_path():
    assert _run([T.INCIDENT_ACTIVATED, T.DISMISS]) is S.IDLE


def test_abort_during_return_then_close():
    path = [
        T.INCIDENT_ACTIVATED, T.LAUNCH, T.SCAN_ALTITUDE_REACHED, T.ROUTE_RECOMMENDED,
        T.ROUTE_LOCKED, T.CART_AT_INCIDENT, T.START_RETURN, T.ABORT,
    ]
    assert _run(path) is S.ABORTED
    assert transition(S.ABORTED, T.CLOSE) is S.IDLE


def test_cannot_abort_at_arrived_or_before_launch():
    for state in (S.IDLE, S.INCIDENT_RECEIVED, S.ARRIVED, S.COMPLETED):
        with pytest.raises(InvalidTransition):
            transition(state, T.ABORT)


def test_arrived_cannot_close_directly():
    with pytest.raises(InvalidTransition):
        transition(S.ARRIVED, T.CLOSE)


# --- commands ---------------------------------------------------------------


@pytest.mark.parametrize(
    "pair", list(itertools.product(Command, S)), ids=lambda p: f"{p[0].value}@{p[1].value}"
)
def test_command_allowed_matrix(pair):
    command, state = pair
    if state in EXPECTED_COMMANDS[command]:
        ensure_command_allowed(state, command)
    else:
        with pytest.raises(InvalidTransition):
            ensure_command_allowed(state, command)


# --- close outcome ----------------------------------------------------------


@pytest.mark.parametrize("outcome", ["resolved", "transferred"])
def test_close_completed_accepts_outcome(outcome):
    assert resolve_close_outcome(S.COMPLETED, outcome) == outcome


@pytest.mark.parametrize("outcome", [None, "aborted", "error", "cancelled", "bogus"])
def test_close_completed_rejects_other_outcomes(outcome):
    with pytest.raises(InvalidOutcome):
        resolve_close_outcome(S.COMPLETED, outcome)


@pytest.mark.parametrize("requested", [None, "resolved", "transferred"])
def test_close_aborted_and_error_ignore_requested(requested):
    assert resolve_close_outcome(S.ABORTED, requested) == "aborted"
    assert resolve_close_outcome(S.ERROR, requested) == "error"


@pytest.mark.parametrize("state", [s for s in S if s not in EXPECTED_COMMANDS[Command.CLOSE]])
def test_close_from_non_terminal_state_is_invalid_transition(state):
    with pytest.raises(InvalidTransition):
        resolve_close_outcome(state, "resolved")
