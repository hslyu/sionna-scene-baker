"""Minimal OSM XML reader for Sionna scene generation."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Node:
    node_id: str
    lat: float
    lon: float
    tags: dict[str, str]


@dataclass(frozen=True)
class Way:
    way_id: str
    node_refs: list[str]
    tags: dict[str, str]

    @property
    def is_closed(self) -> bool:
        return len(self.node_refs) >= 4 and self.node_refs[0] == self.node_refs[-1]


@dataclass(frozen=True)
class RelationMember:
    member_type: str
    ref: str
    role: str


@dataclass(frozen=True)
class Relation:
    relation_id: str
    members: list[RelationMember]
    tags: dict[str, str]


@dataclass(frozen=True)
class OsmData:
    nodes: dict[str, Node]
    ways: dict[str, Way]
    relations: list[Relation]
    bounds: tuple[float, float, float, float]


def read_osm(path: Path) -> OsmData:
    root = ET.parse(path).getroot()
    nodes: dict[str, Node] = {}
    ways: dict[str, Way] = {}
    relations: list[Relation] = []
    bounds = read_bounds(root)

    for element in root:
        if element.tag == "node":
            node_id = element.attrib["id"]
            nodes[node_id] = Node(
                node_id=node_id,
                lat=float(element.attrib["lat"]),
                lon=float(element.attrib["lon"]),
                tags=read_tags(element),
            )
        elif element.tag == "way":
            way_id = element.attrib["id"]
            ways[way_id] = Way(
                way_id=way_id,
                node_refs=[child.attrib["ref"] for child in element if child.tag == "nd"],
                tags=read_tags(element),
            )
        elif element.tag == "relation":
            relations.append(Relation(
                relation_id=element.attrib["id"],
                members=[
                    RelationMember(
                        member_type=child.attrib.get("type", ""),
                        ref=child.attrib.get("ref", ""),
                        role=child.attrib.get("role", ""),
                    )
                    for child in element
                    if child.tag == "member"
                ],
                tags=read_tags(element),
            ))

    return OsmData(nodes=nodes, ways=ways, relations=relations, bounds=bounds)


def read_tags(element: ET.Element) -> dict[str, str]:
    return {
        child.attrib["k"]: child.attrib["v"]
        for child in element
        if child.tag == "tag" and "k" in child.attrib and "v" in child.attrib
    }


def read_bounds(root: ET.Element) -> tuple[float, float, float, float]:
    bounds = root.find("bounds")
    if bounds is None:
        lats = [float(node.attrib["lat"]) for node in root if node.tag == "node"]
        lons = [float(node.attrib["lon"]) for node in root if node.tag == "node"]
        return min(lats), min(lons), max(lats), max(lons)
    return (
        float(bounds.attrib["minlat"]),
        float(bounds.attrib["minlon"]),
        float(bounds.attrib["maxlat"]),
        float(bounds.attrib["maxlon"]),
    )


def parse_meters(value: str | None, default: float | None = None) -> float | None:
    if value is None:
        return default
    match = re.search(r"-?\d+(?:\.\d+)?", value.replace(",", "."))
    if not match:
        return default
    return float(match.group(0))
