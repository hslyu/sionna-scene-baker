#!/usr/bin/env python3
"""Build a Sionna-ready scene from a latitude/longitude bounding box."""

from __future__ import annotations

import argparse
from pathlib import Path

from pyScene.cli import add_build_options, bbox
from pyScene.overpass import DEFAULT_OVERPASS_URL


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download OSM data for a bbox and generate a Sionna-ready scene.",
    )
    parser.add_argument("lat_min", type=float)
    parser.add_argument("lat_max", type=float)
    parser.add_argument("lon_min", type=float)
    parser.add_argument("lon_max", type=float)
    parser.add_argument("terrain_mode", nargs="?", choices=("terrain",))
    parser.add_argument("--out-dir", type=Path, default=Path("data/custom"))
    parser.add_argument("--reuse-osm", action="store_true")
    parser.add_argument("--overpass-url", default=DEFAULT_OVERPASS_URL)
    parser.add_argument("--overpass-timeout", type=int, default=180)
    add_build_options(parser)
    bbox(parser.parse_args())


if __name__ == "__main__":
    main()
