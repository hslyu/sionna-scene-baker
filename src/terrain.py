"""SRTM terrain sampling for local scene generation."""

from __future__ import annotations

import gzip
import math
import re
import struct
from dataclasses import dataclass
from pathlib import Path

from .projection import LocalProjection


@dataclass(frozen=True)
class HgtTile:
    lat_min: int
    lon_min: int
    size: int
    samples: tuple[int, ...]

    @classmethod
    def read(cls, path: Path) -> "HgtTile":
        match = re.match(r"([NS])(\d{2})([EW])(\d{3})\.hgt(?:\.gz)?$", path.name)
        if match is None:
            raise ValueError(f"Unsupported HGT filename: {path}")
        lat_min = int(match.group(2)) * (1 if match.group(1) == "N" else -1)
        lon_min = int(match.group(4)) * (1 if match.group(3) == "E" else -1)

        data = gzip.open(path, "rb").read() if path.suffix == ".gz" else path.read_bytes()
        sample_count = len(data) // 2
        size = int(math.sqrt(sample_count))
        if size * size != sample_count:
            raise ValueError(f"{path} does not contain a square HGT grid")
        return cls(
            lat_min=lat_min,
            lon_min=lon_min,
            size=size,
            samples=struct.unpack(f">{sample_count}h", data),
        )

    def contains(self, lat: float, lon: float) -> bool:
        return self.lat_min <= lat <= self.lat_min + 1 and self.lon_min <= lon <= self.lon_min + 1

    def elevation(self, lat: float, lon: float) -> float:
        row = (self.lat_min + 1 - lat) * (self.size - 1)
        col = (lon - self.lon_min) * (self.size - 1)
        row = min(max(row, 0.0), self.size - 1)
        col = min(max(col, 0.0), self.size - 1)
        r0 = int(math.floor(row))
        c0 = int(math.floor(col))
        r1 = min(r0 + 1, self.size - 1)
        c1 = min(c0 + 1, self.size - 1)
        tr = row - r0
        tc = col - c0

        h00 = self._sample(r0, c0)
        h01 = self._sample(r0, c1)
        h10 = self._sample(r1, c0)
        h11 = self._sample(r1, c1)
        h0 = h00 * (1.0 - tc) + h01 * tc
        h1 = h10 * (1.0 - tc) + h11 * tc
        return h0 * (1.0 - tr) + h1 * tr

    def _sample(self, row: int, col: int) -> int:
        value = self.samples[row * self.size + col]
        return 0 if value == -32768 else value


class TerrainModel:
    def __init__(self, tiles: list[HgtTile], projection: LocalProjection, base_elevation: float) -> None:
        self.tiles = tiles
        self.projection = projection
        self.base_elevation = base_elevation

    @classmethod
    def from_directory(
        cls,
        path: Path,
        projection: LocalProjection,
        center_lat: float,
        center_lon: float,
    ) -> "TerrainModel":
        tiles = [HgtTile.read(tile) for tile in sorted(path.glob("*.hgt*"))]
        if not tiles:
            raise FileNotFoundError(f"No .hgt or .hgt.gz files found in {path}")
        model = cls(tiles, projection, 0.0)
        return cls(tiles, projection, model.elevation(center_lat, center_lon))

    def elevation(self, lat: float, lon: float) -> float:
        for tile in self.tiles:
            if tile.contains(lat, lon):
                return tile.elevation(lat, lon)
        raise ValueError(f"No terrain tile covers lat={lat}, lon={lon}")

    def elevation_xy(self, x: float, y: float) -> float:
        lat, lon = self.projection.unproject(x, y)
        return self.elevation(lat, lon) - self.base_elevation
