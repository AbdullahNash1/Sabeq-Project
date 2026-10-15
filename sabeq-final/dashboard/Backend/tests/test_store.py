from datetime import datetime, timezone

from app.config import Settings
from app.models import IncidentType, LocalizedText, MissionSummary
from app.state.machine import MissionState
from app.state.store import create_default_store
from app.world import load_world

NOW = datetime(2026, 10, 4, 14, 2, tzinfo=timezone.utc)


def _summary(i):
    return MissionSummary(
        id=f"MSN-{i:04d}", reported_at=NOW,
        incident_type=IncidentType(code="fall", label=LocalizedText(en="Fall")),
        zone="A", outcome="resolved",
    )


def _store():
    settings = Settings(_env_file=None)
    return create_default_store(settings, load_world(settings))


def test_boot_state():
    store = _store()
    assert store.state is MissionState.IDLE
    assert store.drone.id == "SBQ-01" and store.drone.docked_on_cart
    assert store.cart.id == "CART-01" and store.cart.state == "at_hub"
    # Cart at the start plaza (0, 0) facing north; the hub is the First Aid Center door (8.6, -15).
    assert (store.cart.position.x, store.cart.position.y, store.cart.heading_deg) == (500.0, 526.5, 0.0)
    assert store.drone.position == store.cart.position
    assert (store.site.hub.x, store.site.hub.y) == (500 + 8.6 * 3.5, 526.5 + 15 * 3.5)
    assert store.camera.stream_url == "/api/drones/SBQ-01/camera/stream"
    assert store.camera.resolution.w == 1280 and store.camera.resolution.h == 960
    assert store.open_incidents().model_dump() == {"active": 0, "queued": 0}


def test_history_keeps_last_10_newest_first():
    store = _store()
    for i in range(1, 14):
        store.add_closed(None, _summary(i), None)
    ids = [m.id for m in store.recent_missions()]
    assert len(ids) == 10
    assert ids[0] == "MSN-0013" and ids[-1] == "MSN-0004"


def test_ids_are_sequential():
    store = _store()
    assert [store.next_mission_id(), store.next_mission_id()] == ["MSN-0001", "MSN-0002"]
    assert store.next_incident_id() == "INC-0001"


def test_snapshot_has_one_drone_and_one_cart():
    snap = _store().snapshot(NOW).model_dump(mode="json")
    assert len(snap["drones"]) == 1 and len(snap["carts"]) == 1
    assert snap["activeMission"] is None and snap["activeIncident"] is None
    assert snap["openIncidents"] == {"active": 0, "queued": 0}
