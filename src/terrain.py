"""SRTM terrain sampling for local scene generation."""

from __future__ import annotations

import gzip
import math
import re
import struct
from dataclasses import dataclass
from pathlib import Path

import requests

from .projection import LocalProjection


DEFAULT_HGT_URL_TEMPLATE = "https://s3.amazonaws.com/elevation-tiles-prod/skadi/{lat_dir}/{tile}.hgt.gz"


def ensure_hgt_tiles(
    path: Path,
    *,
    south: float,
    west: float,
    north: float,
    east: float,
    projection: LocalProjection,
    margin_m: float = 0.0,
    url_template: str = DEFAULT_HGT_URL_TEMPLATE,
    timeout: int = 60,
) -> list[Path]:
    """Download missing HGT tiles needed for a geographic bbox."""
    path.mkdir(parents=True, exist_ok=True)
    tile_names = required_hgt_tile_names(
        south,
        west,
        north,
        east,
        projection=projection,
        margin_m=margin_m,
    )
    paths = []
    for tile_name in tile_names:
        tile_path = path / f"{tile_name}.hgt.gz"
        paths.append(tile_path)
        if tile_path.exists() or tile_path.with_suffix("").exists():
            continue
        lat_dir = tile_name[:3]
        url = url_template.format(lat_dir=lat_dir, tile=tile_name)
        print(f"Downloading terrain tile {tile_name} to {tile_path}")
        response = requests.get(
            url,
            headers={"User-Agent": "pyscene-sionna-builder/0.1"},
            timeout=timeout,
        )
        response.raise_for_status()
        tmp_path = tile_path.with_suffix(tile_path.suffix + ".tmp")
        tmp_path.write_bytes(response.content)
        tmp_path.replace(tile_path)
    return paths


def required_hgt_tile_names(
    south: float,
    west: float,
    north: float,
    east: float,
    *,
    projection: LocalProjection,
    margin_m: float = 0.0,
) -> list[str]:
    south, west, north, east = terrain_bounds_with_margin(
        south,
        west,
        north,
        east,
        projection=projection,
        margin_m=margin_m,
    )
    return [
        hgt_tile_name(lat_min, lon_min)
        for lat_min in tile_floor_range(south, north)
        for lon_min in tile_floor_range(west, east)
    ]


def terrain_bounds_with_margin(
    south: float,
    west: float,
    north: float,
    east: float,
    *,
    projection: LocalProjection,
    margin_m: float,
) -> tuple[float, float, float, float]:
    if margin_m <= 0.0:
        return south, west, north, east

    projected = [
        projection.project(lat, lon)
        for lat in (south, north)
        for lon in (west, east)
    ]
    xs = [point[0] for point in projected]
    ys = [point[1] for point in projected]
    corners = [
        projection.unproject(x, y)
        for x in (min(xs) - margin_m, max(xs) + margin_m)
        for y in (min(ys) - margin_m, max(ys) + margin_m)
    ]
    lats = [south, north, *(lat for lat, _ in corners)]
    lons = [west, east, *(lon for _, lon in corners)]
    return min(lats), min(lons), max(lats), max(lons)


def tile_floor_range(start: float, stop: float) -> range:
    first = math.floor(start)
    last = math.floor(stop)
    if stop > start and math.isclose(stop, float(last), abs_tol=1e-12):
        last -= 1
    return range(first, last + 1)


def hgt_tile_name(lat_min: int, lon_min: int) -> str:
    lat_prefix = "N" if lat_min >= 0 else "S"
    lon_prefix = "E" if lon_min >= 0 else "W"
    return f"{lat_prefix}{abs(lat_min):02d}{lon_prefix}{abs(lon_min):03d}"


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
        return cls(tiles, projection, 0.0)

    def elevation(self, lat: float, lon: float) -> float:
        for tile in self.tiles:
            if tile.contains(lat, lon):
                return tile.elevation(lat, lon)
        raise ValueError(f"No terrain tile covers lat={lat}, lon={lon}")

    def elevation_xy(self, x: float, y: float) -> float:
        lat, lon = self.projection.unproject(x, y)
        return self.elevation(lat, lon) - self.base_elevation
