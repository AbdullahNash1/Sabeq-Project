"""The world files (docs/WORLD.md): loading, the startup check, and parity with the team code."""
import importlib
import json
import math
import sys
from pathlib import Path

import pytest
import yaml

from app.config import Settings
from app.main import create_app
from app.sources.mock.sim import rescue_duration_s
from app.world import CART_STOP_BEFORE_M, WorldConfigError, along, check_routes, load_world, path_length

ROOT = Path(__file__).resolve().parents[2]
REAL_ROUTES = ROOT / "ros_ws/src/sabeq_ros/config/routes.yaml"
REAL_META = ROOT / "docs/team/world_meta.json"


@pytest.fixture(scope="module")
def world():
    return load_world(Settings(_env_file=None))


def _team_module(name):
    """Import a read-only script from docs/team or tools without writing __pycache__ there."""
    sys.dont_write_bytecode = True
    for d in (ROOT / "docs/team", ROOT / "tools"):
        if str(d) not in sys.path:
            sys.path.insert(0, str(d))
    return importlib.import_module(name)


def _broken_routes(tmp_path, mutate) -> Settings:
    config = yaml.safe_load(REAL_ROUTES.read_text())
    mutate(config)
    path = tmp_path / "routes.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    return Settings(_env_file=None, routes_yaml=str(path))


# --- loading ---------------------------------------------------------------------


def test_routes_come_from_the_files(world):
    assert world.route_ids == ["A_west", "B_north", "C_east"]
    assert {r: (round(w.length_m, 2), w.scan_yaw_ned_deg, w.scan_people, w.label) for r, w in world.routes.items()} == {
        "A_west": (151.2, 315.0, 18, {"en": "A · West", "ar": "A · غرب"}),
        "B_north": (112.0, 0.0, 26, {"en": "B · North", "ar": "B · شمال"}),
        "C_east": (151.2, 45.0, 10, {"en": "C · East", "ar": "C · شرق"}),
    }
    assert world.start == (0.0, 0.0) and world.incident == (0.0, 112.0) and world.home == (0.0, -0.1)
    assert world.first_aid_door == (8.6, -15.0) and world.first_aid_stop == (0.0, -15.0)


def test_default_paths_resolve_from_the_backend_dir_not_the_cwd(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    settings = Settings(_env_file=None)
    assert settings.resolve_path(settings.routes_yaml) == REAL_ROUTES.resolve()
    assert settings.resolve_path(settings.world_meta) == REAL_META.resolve()
    assert load_world(settings).route_ids == ["A_west", "B_north", "C_east"]


def test_paths_match_demo_common(world):
    """cart_path / return_path are ports of demo_common.build_path / build_return_path."""
    dc = _team_module("demo_common")
    meta = json.loads(REAL_META.read_text())
    for rid in world.route_ids:
        want = [tuple(map(float, p)) for p in dc.build_path(meta, rid)]
        assert world.cart_path(rid) == [p for i, p in enumerate(want) if i == 0 or p != want[i - 1]]
        assert world.return_path(rid) == [tuple(map(float, p)) for p in dc.build_return_path(meta, rid)]
        e, n, _ = along(world.cart_path(rid), world.routes[rid].length_m - CART_STOP_BEFORE_M)
        assert world.return_path(rid)[0] == pytest.approx((e, n))  # the return starts where the cart stopped
        assert world.return_path(rid)[-1] == world.first_aid_stop
    assert [round(path_length(world.return_path(r)), 2) for r in world.route_ids] == [161.2, 122.0, 161.2]


def test_rescue_timing_follows_sim_node(world):
    """sim_node.Rescue on C_east: the cart stops at (5, 112) heading west, the bed is at (7.3, 112),
    the medics hop off to (7.3, 114.2) and walk 7.40 m to (0, 113) and back at 3 m/s,
    plus 1.5 s treat and 0.8 s load."""
    path = world.cart_path("C_east")
    e, n, h = along(path, path_length(path) - CART_STOP_BEFORE_M)
    assert (e, n) == pytest.approx((5.0, 112.0))
    assert rescue_duration_s((e, n), h, world.patient) == pytest.approx(2 * math.hypot(7.3, 1.2) / 3 + 2.3)


# --- the startup check -------------------------------------------------------------


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (lambda c: c["routes"].update({"D_south": c["routes"].pop("C_east")}), "route names"),
        (lambda c: c.update(routes={k: c["routes"][k] for k in ("B_north", "A_west", "C_east")}), "route names"),
        (lambda c: c.update(scan_order=["C_east", "B_north", "A_west"]), "scan_order"),
        (lambda c: c["routes"]["B_north"]["waypoints"].__setitem__(1, [0.0, 97.0]), "B_north: routes.yaml waypoints"),
        (lambda c: c.update(incident=[96.0, 0.0]), "incident"),
        (lambda c: c.update(start=[1.0, 0.0]), "start"),
    ],
)
def test_mismatched_routes_fail_at_startup(tmp_path, mutate, expected):
    settings = _broken_routes(tmp_path, mutate)
    with pytest.raises(WorldConfigError, match=expected) as exc:
        create_app(settings)
    assert "tools/check_routes.py" in str(exc.value)


def test_old_world_routes_are_rejected(tmp_path):
    """The v1.3 routes.yaml (A_north / B_center / C_south, incident at (96, 0))."""
    old = {
        "frame": "gazebo_world", "start": [0.0, 0.0], "incident": [96.0, 0.0],
        "scan_order": ["A_north", "B_center", "C_south"],
        "routes": {n: {"waypoints": [[0.0, 0.0], [96.0, 0.0]]} for n in ("A_north", "B_center", "C_south")},
    }
    path = tmp_path / "routes.yaml"
    path.write_text(yaml.safe_dump(old))
    with pytest.raises(WorldConfigError, match=r"\['A_north', 'B_center', 'C_south'\]"):
        load_world(Settings(_env_file=None, routes_yaml=str(path)))


def test_every_route_needs_a_label():
    with pytest.raises(WorldConfigError, match=r"ROUTE_LABELS has no label for \['C_east'\]"):
        load_world(Settings(_env_file=None, route_labels={"A_west": {"en": "A"}, "B_north": {"ar": "B"}}))


def test_route_label_needs_some_text():
    labels = {"A_west": {"en": "A"}, "B_north": {"en": ""}, "C_east": {"fr": "C"}}
    with pytest.raises(WorldConfigError) as exc:
        load_world(Settings(_env_file=None, route_labels=labels))
    assert "ROUTE_LABELS['B_north']" in str(exc.value) and "ROUTE_LABELS['C_east']" in str(exc.value)


def test_route_labels_from_the_environment(monkeypatch):
    monkeypatch.setenv("ROUTE_LABELS", '{"A_west": {"en": "West", "ar": "غرب"}, "B_north": {"en": "North"},'
                                        ' "C_east": {"ar": "شرق"}}')
    world = load_world(Settings(_env_file=None))
    assert world.routes["A_west"].label == {"en": "West", "ar": "غرب"}
    assert world.routes["C_east"].label == {"ar": "شرق"}


def test_missing_file_is_a_clear_error(tmp_path):
    with pytest.raises(WorldConfigError, match="WORLD_META not found"):
        load_world(Settings(_env_file=None, world_meta=str(tmp_path / "nope.json")))


def test_same_rules_as_tools_check_routes(tmp_path):
    """app.world.check_routes and tools/check_routes.py agree, on the real files and on a broken one."""
    tool = _team_module("check_routes")
    meta = json.loads(REAL_META.read_text())
    assert check_routes(yaml.safe_load(REAL_ROUTES.read_text()), meta) == []
    assert tool.check(REAL_ROUTES, REAL_META)[0] == []

    settings = _broken_routes(tmp_path, lambda c: c["routes"]["A_west"]["waypoints"].pop(1))
    broken = settings.resolve_path(settings.routes_yaml)
    ours = check_routes(yaml.safe_load(broken.read_text()), meta)
    theirs = tool.check(broken, REAL_META)[0]
    assert len(ours) == len(theirs) == 1 and ours[0].startswith("A_west") and theirs[0].startswith("A_west")
