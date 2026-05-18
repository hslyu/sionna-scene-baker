"""Local geographic projection utilities."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class LocalProjection:
    """Small-area Transverse Mercator projection centered on one lat/lon."""

    lat0: float
    lon0: float
    radius: float = 6378137.0
    scale: float = 1.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "_lat0_rad", math.radians(self.lat0))

    def project(self, lat: float, lon: float) -> tuple[float, float]:
        lat_rad = math.radians(lat)
        lon_rad = math.radians(lon - self.lon0)
        b = math.sin(lon_rad) * math.cos(lat_rad)
        x = 0.5 * self.scale * self.radius * math.log((1.0 + b) / (1.0 - b))
        y = self.scale * self.radius * (
            math.atan(math.tan(lat_rad) / math.cos(lon_rad)) - self._lat0_rad
        )
        return x, y

    def unproject(self, x: float, y: float) -> tuple[float, float]:
        x_scaled = x / (self.scale * self.radius)
        d = y / (self.scale * self.radius) + self._lat0_rad
        lat = math.asin(math.sin(d) / math.cosh(x_scaled))
        lon_delta = math.atan2(math.sinh(x_scaled), math.cos(d))
        return math.degrees(lat), self.lon0 + math.degrees(lon_delta)


def projection_from_bounds(
    south: float,
    west: float,
    north: float,
    east: float,
) -> LocalProjection:
    return LocalProjection(lat0=(south + north) * 0.5, lon0=(west + east) * 0.5)
