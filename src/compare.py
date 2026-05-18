"""Compare two Sionna Mitsuba XML scenes at the mesh/statistics level."""

from __future__ import annotations

import argparse
import json
import math
import struct
import xml.etree.ElementTree as ET
from pathlib import Path


def scene_summary(scene_xml: Path) -> dict:
    root = ET.parse(scene_xml).getroot()
    materials = {
        bsdf.attrib["id"]: bsdf.attrib["type"]
        for bsdf in root.findall("./bsdf")
    }
    shapes = {}
    for shape in root.findall("./shape"):
        filename = shape.find("./string[@name='filename']")
        ref = shape.find("./ref[@name='bsdf']")
        if filename is None:
            continue
        mesh_path = scene_xml.parent / filename.attrib["value"]
        shapes[semantic_name(mesh_path.stem)] = {
            "shape_id": shape.attrib.get("id"),
            "shape_type": shape.attrib.get("type"),
            "filename": filename.attrib["value"],
            "material": ref.attrib.get("id") if ref is not None else None,
            "mesh": mesh_summary(mesh_path),
        }
    return {
        "scene": str(scene_xml),
        "version": root.attrib.get("version"),
        "materials": materials,
        "shapes": shapes,
    }


def semantic_name(stem: str) -> str:
    if stem.startswith("map_osm_"):
        stem = stem.removesuffix("_elm__5")
        stem = stem.removesuffix("_elm__6")
        stem = stem.removesuffix("_elm__7")
        stem = stem.removesuffix("_elm__8")
        for token in ("_elm__10", "_elm__12", "_elm__15", "_elm__18", "_elm__21", "_elm__24",
                      "_elm__27", "_elm__30", "_elm__33", "_elm__36", "_elm__39", "_elm__41",
                      "_elm__43", "_elm__46", "_elm__48"):
            stem = stem.replace(token, "")
    if stem.startswith("Plane"):
        return "Plane"
    return stem


def mesh_summary(path: Path) -> dict:
    with path.open("rb") as f:
        header = []
        while True:
            line = f.readline()
            if not line:
                raise ValueError(f"{path} ended before PLY header finished")
            header.append(line.decode("ascii").strip())
            if line == b"end_header\n":
                break

        vertex_count = 0
        face_count = 0
        vertex_types = []
        in_vertex = False
        for line in header:
            parts = line.split()
            if parts[:2] == ["element", "vertex"]:
                vertex_count = int(parts[2])
                in_vertex = True
            elif parts[:2] == ["element", "face"]:
                face_count = int(parts[2])
                in_vertex = False
            elif parts and parts[0] == "element":
                in_vertex = False
            elif in_vertex and parts[:1] == ["property"]:
                vertex_types.append(parts[1])

        formats = {"float": "f", "float32": "f", "double": "d", "float64": "d"}
        fmt = "<" + "".join(formats.get(t, "f") for t in vertex_types)
        step = struct.calcsize(fmt)
        mins = [math.inf, math.inf, math.inf]
        maxs = [-math.inf, -math.inf, -math.inf]
        for _ in range(vertex_count):
            values = struct.unpack(fmt, f.read(step))
            for i in range(3):
                mins[i] = min(mins[i], values[i])
                maxs[i] = max(maxs[i], values[i])
    return {
        "vertices": vertex_count,
        "faces": face_count,
        "bbox_min": mins,
        "bbox_max": maxs,
    }


def compare(reference: dict, candidate: dict) -> dict:
    ref_shapes = reference["shapes"]
    cand_shapes = candidate["shapes"]
    shape_names = sorted(set(ref_shapes) | set(cand_shapes))
    shape_diffs = {}
    for name in shape_names:
        ref = ref_shapes.get(name)
        cand = cand_shapes.get(name)
        if ref is None or cand is None:
            shape_diffs[name] = {"status": "missing", "reference": ref is not None, "candidate": cand is not None}
            continue
        ref_mesh = ref["mesh"]
        cand_mesh = cand["mesh"]
        shape_diffs[name] = {
            "status": "compared",
            "material": [ref["material"], cand["material"]],
            "vertices": [ref_mesh["vertices"], cand_mesh["vertices"]],
            "faces": [ref_mesh["faces"], cand_mesh["faces"]],
            "bbox_min_diff": [cand_mesh["bbox_min"][i] - ref_mesh["bbox_min"][i] for i in range(3)],
            "bbox_max_diff": [cand_mesh["bbox_max"][i] - ref_mesh["bbox_max"][i] for i in range(3)],
        }
    return {
        "reference": reference["scene"],
        "candidate": candidate["scene"],
        "version": [reference["version"], candidate["version"]],
        "materials": [reference["materials"], candidate["materials"]],
        "shapes": shape_diffs,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("reference_scene", type=Path)
    parser.add_argument("candidate_scene", type=Path)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()

    result = compare(scene_summary(args.reference_scene), scene_summary(args.candidate_scene))
    text = json.dumps(result, indent=2)
    if args.json:
        args.json.write_text(text + "\n")
        print(f"Wrote {args.json}")
    else:
        print(text)


if __name__ == "__main__":
    main()
