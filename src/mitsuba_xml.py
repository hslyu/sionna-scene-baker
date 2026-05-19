"""Mitsuba XML writer for Sionna scenes."""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from pathlib import Path

from .geometry import Mesh
from .materials import MATERIALS, make_material_xml, material_for_mesh


SHAPE_IDS = {
    "map_osm_buildings-wall": "py_buildings_wall",
    "map_osm_buildings-roof": "py_buildings_roof",
    "map_osm_water": "py_water",
    "map_osm_forest": "py_forest",
    "map_osm_vegetation": "py_vegetation",
    "map_osm_areas_park": "py_areas_park",
    "map_osm_areas_pedestrian": "py_areas_pedestrian",
    "map_osm_areas_steps": "py_areas_steps",
    "map_osm_roads_primary": "py_roads_primary",
    "map_osm_roads_trunk": "py_roads_trunk",
    "map_osm_roads_secondary": "py_roads_secondary",
    "map_osm_roads_tertiary": "py_roads_tertiary",
    "map_osm_roads_residential": "py_roads_residential",
    "map_osm_roads_service": "py_roads_service",
    "map_osm_roads_unclassified": "py_roads_unclassified",
    "map_osm_roads_track": "py_roads_track",
    "map_osm_roads_pedestrian": "py_roads_pedestrian",
    "map_osm_paths_footway": "py_paths_footway",
    "map_osm_paths_cycleway": "py_paths_cycleway",
    "map_osm_paths_steps": "py_paths_steps",
    "Plane": "py_ground",
}


def use_face_normals(mesh_name: str) -> bool:
    return "building" in mesh_name or "roof" in mesh_name


def write_scene_xml(xml_path: Path, mesh_dir: Path, meshes: dict[str, Mesh]) -> None:
    xml_path.parent.mkdir(parents=True, exist_ok=True)
    root = ET.Element("scene", {"version": "3.0.0"})
    ET.SubElement(root, "integrator", {"type": "path"})
    for material_id, params in MATERIALS.items():
        root.append(make_material_xml(material_id, params))

    for mesh_name in sorted(meshes):
        mesh_path = mesh_dir / f"{mesh_name}.ply"
        shape = ET.SubElement(root, "shape", {
            "type": "ply",
            "id": SHAPE_IDS.get(mesh_name, f"py_{mesh_name}"),
        })
        ET.SubElement(shape, "string", {
            "name": "filename",
            "value": Path(os.path.relpath(mesh_path, xml_path.parent)).as_posix(),
        })
        ET.SubElement(shape, "boolean", {
            "name": "face_normals",
            "value": str(use_face_normals(mesh_name)).lower(),
        })
        ET.SubElement(shape, "ref", {
            "id": f"mat-{material_for_mesh(mesh_name)}",
            "name": "bsdf",
        })

    ET.indent(root, space="    ")
    ET.ElementTree(root).write(xml_path, encoding="utf-8", xml_declaration=True)
