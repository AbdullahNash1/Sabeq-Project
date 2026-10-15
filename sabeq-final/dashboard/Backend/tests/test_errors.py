"""Error envelope (§1.1). Test-only routes are mounted on a fresh app; the real app has none."""
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.errors import ApiError
from app.config import Settings
from tests.conftest import manual_app
from app.models import SelectRouteRequest
from app.state.machine import InvalidOutcome, InvalidTransition, MissionState


def _client():
    app = manual_app(Settings(_env_file=None))

    @app.post("/_test/route")
    def _route(body: SelectRouteRequest):
        return body

    @app.get("/_test/transition")
    def _transition():
        raise InvalidTransition(MissionState.ESCORTING, "launch")

    @app.get("/_test/outcome")
    def _outcome():
        raise InvalidOutcome("outcome must be 'resolved' or 'transferred'")

    @app.get("/_test/api-error")
    def _api_error():
        raise ApiError(503, "drone_unavailable", "Drone is not docked")

    @app.get("/_test/http")
    def _http():
        raise HTTPException(404, "Mission not found")

    @app.get("/_test/crash")
    def _crash():
        raise RuntimeError("boom")

    return TestClient(app, raise_server_exceptions=False)


def _assert_envelope(resp, status, code):
    assert resp.status_code == status
    body = resp.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == code
    assert isinstance(body["error"]["message"], str)
    assert isinstance(body["error"]["details"], dict)
    return body["error"]


def test_validation_error_is_400_not_422():
    err = _assert_envelope(_client().post("/_test/route", json={"route": ""}), 400, "bad_request")
    assert err["details"]["errors"][0]["loc"] == ["body", "route"]


def test_unknown_field_is_400():
    _assert_envelope(_client().post("/_test/route", json={"route": "A", "x": 1}), 400, "bad_request")


def test_malformed_json_is_400():
    resp = _client().post("/_test/route", content="{", headers={"content-type": "application/json"})
    _assert_envelope(resp, 400, "bad_request")


def test_invalid_transition_is_409():
    err = _assert_envelope(_client().get("/_test/transition"), 409, "invalid_transition")
    assert err["message"] == "Cannot launch from state 'escorting'"


def test_invalid_outcome_is_400():
    _assert_envelope(_client().get("/_test/outcome"), 400, "bad_request")


def test_api_error_keeps_contract_code():
    _assert_envelope(_client().get("/_test/api-error"), 503, "drone_unavailable")


def test_http_exception_and_unknown_path():
    _assert_envelope(_client().get("/_test/http"), 404, "not_found")
    _assert_envelope(_client().get("/nope"), 404, "not_found")


def test_unhandled_exception_uses_envelope():
    _assert_envelope(_client().get("/_test/crash"), 500, "internal_error")
