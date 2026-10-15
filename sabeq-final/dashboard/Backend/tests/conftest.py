"""Shared fixtures. `drive_to()` reaches any mission state: REST for commands, and the
service's event hooks (run on the app's event loop via client.portal) for the ROS
milestones that have no data source yet."""
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models import Position, Route
from app.sources.null import null_sources

INCIDENT_BODY = {
    "severity": "critical",
    "type": "cardiac_arrest",
    "title": {"en": "Suspected cardiac arrest", "ar": "اشتباه توقف القلب"},
    "location": {
        "zone": "B",
        "description": {"en": "Zone B · near Pavilion B-14"},
        "x": 750,
        "y": 235,
    },
    "source": "staff_radio",
}

# Three routes with ids outside the world (route ids stay dynamic in the API). B is fastest.
EST = {"A": 218.0, "B": 125.0, "C": 170.0}
PEOPLE = {"A": 186, "B": 38, "C": 61}


def make_routes(ids=("A", "B", "C")):
    """Routes with ids outside the world (the API keeps route ids dynamic); the service then
    uses each route's own polyline as its path."""
    a = Position(lat=0, lon=0, x=500, y=526.5)  # world (0, 0)
    b = Position(lat=0, lon=0, x=500, y=134.5)  # world (0, 112)
    return [
        Route(id=i, label={"en": f"Route {i}", "ar": f"المسار {i}"}, length_m=112, people_count=PEOPLE.get(i, 1), count_final=True,
              est_time_s=EST.get(i, 200.0), polyline=[a, b])
        for i in ids
    ]


@pytest.fixture
def settings():
    return Settings(
        _env_file=None,
        auto_select_seconds=60,  # long: tests trigger selection explicitly unless overridden
        system_status_interval_s=3600,  # keep periodic messages out of WS assertions
    )


def manual_app(settings):
    """App whose sources do nothing: tests drive milestones through the service hooks."""
    return create_app(settings, sources_factory=null_sources, record_commands=True)


@pytest.fixture
def app(settings):
    return manual_app(settings)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


@pytest.fixture
def svc(app):
    return app.state.service


@pytest.fixture
def ros(app):
    """Commands the service published, in ROS wire format."""
    return app.state.recorder


class Driver:
    def __init__(self, client, svc):
        self.client, self.svc = client, svc

    def on_loop(self, fn, *args):
        return self.client.portal.call(fn, *args)

    def create_incident(self, **overrides):
        body = {**INCIDENT_BODY, **overrides}
        resp = self.client.post("/api/incidents", json=body)
        assert resp.status_code == 201, resp.text
        return resp.json()

    def post(self, mission_id, action, body=None):
        return self.client.post(f"/api/missions/{mission_id}/{action}", json=body)

    def ok(self, mission_id, action, body=None):
        resp = self.post(mission_id, action, body)
        assert resp.status_code == 200, resp.text
        return resp.json()

    def recommend(self, recommended="B", ids=("A", "B", "C")):
        return self.on_loop(self.svc.route_recommended, make_routes(ids), recommended)

    def drive_to(self, state):
        """Create an incident and drive its mission to `state`; returns the mission id."""
        mid = self.create_incident()["missionId"]
        steps = {
            "incident_received": [],
            "taking_off": ["launch"],
            "scanning": ["launch", "alt"],
            "route_selected": ["launch", "alt", "rec"],
            "escorting": ["launch", "alt", "rec", "lock"],
            "arrived": ["launch", "alt", "rec", "lock", "at_incident"],
            "returning": ["launch", "alt", "rec", "lock", "at_incident", "loaded", "return"],
            "completed": ["launch", "alt", "rec", "lock", "at_incident", "loaded", "return", "at_hub", "landed"],
            "aborted": ["launch", "abort"],
            "error": ["launch", "fault"],
        }[state]
        for step in steps:
            if step == "launch":
                self.ok(mid, "launch")
            elif step == "alt":
                self.on_loop(self.svc.scan_altitude_reached)
            elif step == "rec":
                self.recommend()
            elif step == "lock":
                self.ok(mid, "route", {"route": "B"})
            elif step == "at_incident":
                self.on_loop(self.svc.cart_at_incident)
            elif step == "return":
                self.ok(mid, "return")
            elif step == "loaded":
                self.on_loop(self.svc.patient_loaded)
            elif step == "at_hub":
                self.on_loop(self.svc.cart_at_hub)
            elif step == "landed":
                self.on_loop(self.svc.drone_landed)
            elif step == "abort":
                self.ok(mid, "abort", {"reason": "operator_abort"})
            elif step == "fault":
                self.on_loop(self.svc.fault, "link_lost", "No /drone/status for 5 s")
        assert self.client.get("/api/missions/active").json()["state"] == state
        return mid


@pytest.fixture
def drv(client, svc):
    return Driver(client, svc)
