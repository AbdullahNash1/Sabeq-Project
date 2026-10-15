"""Coordinate projection (API_CONTRACT §2): Gazebo world ENU metres <-> lat/lon <-> SVG x/y.

The defaults (app/config.py) fit the final world's site bounds into the 1000 x 640 map,
north up. Mission geometry itself is in metres (app/world.py); positions are projected
only when they are reported.
"""
import math

from app.config import Settings
from app.models import Position

M_PER_DEG_LAT = 111_320.0


class ProjectionNotConfigured(Exception):
    pass


class Projection:
    def __init__(self, settings: Settings):
        self.lat0 = settings.proj_lat0
        self.lon0 = settings.proj_lon0
        self.x0 = settings.proj_x0
        self.y0 = settings.proj_y0
        self.scale = settings.proj_scale
        theta = math.radians(settings.proj_rotation_deg)
        self._cos, self._sin = math.cos(theta), math.sin(theta)

    @property
    def configured(self) -> bool:
        return None not in (self.lat0, self.lon0, self.x0, self.y0, self.scale)

    def _require(self) -> None:
        if not self.configured:
            raise ProjectionNotConfigured(
                "coordinate projection is not configured (SABEQ_PROJ_*)"
            )

    # --- local metres <-> lat/lon / SVG -------------------------------------

    def local_to_position(self, e: float, n: float) -> Position:
        self._require()
        lat = self.lat0 + n / M_PER_DEG_LAT
        lon = self.lon0 + e / (M_PER_DEG_LAT * math.cos(math.radians(self.lat0)))
        e_rot = e * self._cos - n * self._sin
        n_rot = e * self._sin + n * self._cos
        return Position(lat=lat, lon=lon, x=self.x0 + self.scale * e_rot, y=self.y0 - self.scale * n_rot)

    def latlon_to_local(self, lat: float, lon: float) -> tuple:
        self._require()
        n = (lat - self.lat0) * M_PER_DEG_LAT
        e = (lon - self.lon0) * M_PER_DEG_LAT * math.cos(math.radians(self.lat0))
        return e, n

    def svg_to_local(self, x: float, y: float) -> tuple:
        self._require()
        e_rot = (x - self.x0) / self.scale
        n_rot = (self.y0 - y) / self.scale
        return e_rot * self._cos + n_rot * self._sin, -e_rot * self._sin + n_rot * self._cos

    def position_to_local(self, p: Position) -> "tuple | None":
        return self.svg_to_local(p.x, p.y) if self.configured else None
