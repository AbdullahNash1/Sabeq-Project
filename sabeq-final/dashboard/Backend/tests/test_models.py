from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.models import (
    CreateIncident,
    EscortInfo,
    Mission,
    PayloadRequest,
    PayloadState,
    Position,
    Route,
    ScanInfo,
)
from app.models.ws import MissionStateEvent, RouteSelected, make_envelope
from app.state.machine import MissionState

NOW = datetime(2026, 10, 4, 14, 2, tzinfo=timezone.utc)
P = Position(lat=24.96, lon=46.65, x=750, y=235)


def _route(route_id, people=10):
    return Route(
        id=route_id, label={"en": f"Route {route_id}"}, length_m=500, people_count=people, count_final=False,
        polyline=[P, P],
    )


def _mission(route_ids, **kw):
    return Mission(
        id="MSN-0001", incident_id="INC-0001", drone_id="SBQ-01", cart_id="CART-01",
        state=MissionState.SCANNING, state_entered_at=NOW, scan_altitude_m=60,
        scan=ScanInfo(), routes=[_route(r) for r in route_ids], escort=EscortInfo(),
        payload=PayloadState(speaker=True, led=True), timeline=[], created_at=NOW, **kw,
    )


def test_camel_case_on_the_wire():
    data = _mission(["A", "B", "C"], recommended_route="B").model_dump(mode="json")
    assert {"incidentId", "stateEnteredAt", "scanAltitudeM", "recommendedRoute", "autoSelectAt"} <= set(data)
    assert data["routes"][0]["peopleCount"] == 10
    assert data["state"] == "scanning"
    assert "incident_id" not in data


def test_accepts_camel_and_snake_input():
    assert Route.model_validate(
        {"id": "C", "label": {"en": "C · East", "ar": "C · شرق"}, "lengthM": 1, "peopleCount": 2, "countFinal": True, "crowdFactor": 1, "polyline": []}
    ).people_count == 2
    assert _route("C").length_m == 500


@pytest.mark.parametrize("route_ids", [["A"], ["A", "B", "C"], ["A", "B", "C", "D", "E"], ["north", "south"]])
def test_route_ids_are_dynamic(route_ids):
    m = _mission(route_ids, recommended_route=route_ids[-1], selected_route=route_ids[0])
    assert [r.id for r in m.routes] == route_ids


def test_route_reference_must_be_in_list():
    with pytest.raises(ValidationError, match="not one of"):
        _mission(["A", "B", "C"], selected_route="Z")


def test_counts_keyed_by_any_route_id():
    from app.models import CameraDetections, ImageSize

    d = CameraDetections(
        drone_id="SBQ-01", mode="scan_360", frame_stamp=NOW, image_size=ImageSize(w=1280, h=960),
        boxes=[], counts={"A": 1, "B": 2, "C": 3},
    )
    assert d.model_dump(mode="json")["counts"] == {"A": 1, "B": 2, "C": 3}


def test_request_rejects_unknown_fields():
    body = {
        "severity": "critical", "type": "cardiac_arrest", "title": {"en": "x"},
        "location": {"zone": "B", "description": {"en": "y"}, "x": 1, "y": 2},
        "source": "operator", "externalRef": "RADIO-1",
    }
    with pytest.raises(ValidationError, match="externalRef"):
        CreateIncident.model_validate(body)
    del body["externalRef"]
    assert CreateIncident.model_validate(body).location.x == 1


def test_create_incident_needs_a_coordinate_pair():
    with pytest.raises(ValidationError, match="lat/lon or x/y"):
        CreateIncident.model_validate({
            "severity": "low", "type": "t", "title": {"ar": "x"},
            "location": {"zone": "A", "description": {"en": "d"}, "lat": 1},
            "source": "dispatch",
        })


def test_payload_request_needs_one_field():
    with pytest.raises(ValidationError):
        PayloadRequest.model_validate({})
    assert PayloadRequest.model_validate({"led": False}).led is False


def test_mission_state_event_uses_from_key():
    event = MissionStateEvent.model_validate(
        {"missionId": "MSN-0001", "from": "scanning", "to": "route_selected", "mission": _mission(["A"])}
    )
    dumped = event.model_dump(mode="json")
    assert dumped["from"] == "scanning" and dumped["to"] == "route_selected"


def test_envelope_shape_and_type_check():
    env = make_envelope("route.selected", RouteSelected(mission_id="M", route="C", source="operator"), NOW)
    dumped = env.model_dump(mode="json")
    assert set(dumped) == {"type", "data", "ts"}
    assert dumped["data"] == {"missionId": "M", "route": "C", "source": "operator"}
    assert dumped["ts"].startswith("2026-10-04T14:02:00")
    with pytest.raises(ValueError):
        make_envelope("route.counts", RouteSelected(mission_id="M", route="C", source="auto"))
    with pytest.raises(TypeError):
        make_envelope("pong", RouteSelected(mission_id="M", route="C", source="auto"))
