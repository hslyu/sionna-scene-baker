"""Overpass API download helpers."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import requests


DEFAULT_OVERPASS_URL = "https://overpass-api.de/api/interpreter"


def download_osm(
    path: Path,
    *,
    south: float,
    west: float,
    north: float,
    east: float,
    overpass_url: str = DEFAULT_OVERPASS_URL,
    timeout: int = 180,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    bbox = f"{south},{west},{north},{east}"
    query = f"""
[out:xml][timeout:{timeout}];
(
  node({bbox});
  way({bbox});
  relation({bbox});
);
(._;>;);
out body;
"""
    response = requests.post(
        overpass_url,
        data={"data": query},
        headers={"User-Agent": "pyscene-sionna-builder/0.1"},
        timeout=timeout + 30,
    )
    response.raise_for_status()

    root = ET.fromstring(response.content)
    bounds = root.find("bounds")
    if bounds is None:
        bounds = ET.Element("bounds")
        root.insert(0, bounds)
    bounds.attrib.update({
        "minlat": str(south),
        "minlon": str(west),
        "maxlat": str(north),
        "maxlon": str(east),
    })
    ET.indent(root, space="  ")
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
