"""Scene geometry queries for loaded Sionna scenes."""

from __future__ import annotations

import struct
import weakref
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import numpy as np


GROUND_MATERIALS = {"mat-ground"}
VEGETATION_MATERIALS = {"mat-vegetation", "mat-park"}
BUILDING_ROOF_MATERIALS = {"mat-building_roof"}
IGNORED_OCCUPANCY_MATERIALS = GROUND_MATERIALS | VEGETATION_MATERIALS

PLY_SCALAR_TYPES = {
    "char": "b",
    "int8": "b",
    "uchar": "B",
    "uint8": "B",
    "short": "h",
    "int16": "h",
    "ushort": "H",
    "uint16": "H",
    "int": "i",
    "int32": "i",
    "uint": "I",
    "uint32": "I",
    "float": "f",
    "float32": "f",
    "double": "d",
    "float64": "d",
}

_SCENE_XMLS: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()
_SCENE_INDICES: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


@dataclass(frozen=True)
class Mesh:
    vertices: np.ndarray
    faces: np.ndarray
    material_id: str
    shape_id: str
    path: Path


@dataclass(frozen=True)
class TriangleIndex:
    vertices: np.ndarray
    faces: np.ndarray
    bbox_min: np.ndarray
    bbox_max: np.ndarray


@dataclass(frozen=True)
class SceneIndex:
    ground: TriangleIndex
    buildings: TriangleIndex | None
    objects: TriangleIndex | None


def register(scene, scene_xml: Path) -> None:
    path = Path(scene_xml).resolve()
    try:
        scene._scenebaker_scene_xml = path
        return
    except AttributeError:
        pass
    try:
        _SCENE_XMLS[scene] = path
    except TypeError:
        pass


def bounds(scene) -> tuple[float, float, float, float]:
    index = _index(scene)
    vertices = index.ground.vertices
    return (
        float(np.min(vertices[:, 0])),
        float(np.max(vertices[:, 0])),
        float(np.min(vertices[:, 1])),
        float(np.max(vertices[:, 1])),
    )


def height(scene, x: float, y: float, buildings: bool = False) -> float | None:
    index = _index(scene)
    building_z = None
    if buildings and index.buildings is not None:
        building_z = _z_at(index.buildings, x, y)
    if building_z is not None:
        return building_z
    return _z_at(index.ground, x, y)


def occupied(scene, x: float, y: float, height: float = 2.0) -> bool:
    index = _index(scene)
    if index.objects is None:
        return False
    ground_z = _z_at(index.ground, x, y)
    object_z = _z_at(index.objects, x, y)
    if ground_z is None or object_z is None:
        return False
    return object_z - ground_z >= height


def _index(scene) -> SceneIndex:
    cached = _cached_index(scene)
    if cached is not None:
        return cached

    scene_xml = _scene_xml(scene)
    meshes = _read_scene_meshes(scene_xml)
    ground_meshes = [mesh for mesh in meshes if _is_ground(mesh)]
    if not ground_meshes:
        raise ValueError(f"could not identify a ground mesh in {scene_xml}")

    index = SceneIndex(
        ground=_triangle_index(ground_meshes),
        buildings=_optional_triangle_index(mesh for mesh in meshes if _is_building_roof(mesh)),
        objects=_optional_triangle_index(mesh for mesh in meshes if not _is_ignored_occupancy(mesh)),
    )
    try:
        scene._scenebaker_index = index
    except AttributeError:
        try:
            _SCENE_INDICES[scene] = index
        except TypeError:
            pass
    return index


def _cached_index(scene) -> SceneIndex | None:
    index = getattr(scene, "_scenebaker_index", None)
    if index is not None:
        return index
    try:
        return _SCENE_INDICES.get(scene)
    except TypeError:
        return None


def _scene_xml(scene) -> Path:
    scene_xml = getattr(scene, "_scenebaker_scene_xml", None)
    if scene_xml is not None:
        return Path(scene_xml).resolve()
    try:
        return _SCENE_XMLS[scene]
    except (KeyError, TypeError):
        raise ValueError(
            "scene geometry metadata is unavailable; load the scene with scenebaker.load_scene"
        ) from None


def _read_scene_meshes(scene_xml: Path) -> list[Mesh]:
    root = ET.parse(scene_xml).getroot()
    meshes = []
    for shape in root.findall("./shape"):
        filename = shape.find("./string[@name='filename']")
        if filename is None:
            continue
        bsdf = shape.find("./ref[@name='bsdf']")
        material_id = bsdf.attrib.get("id", "") if bsdf is not None else ""
        mesh_path = scene_xml.parent / filename.attrib["value"]
        vertices, faces = _read_ply(mesh_path)
        meshes.append(Mesh(
            vertices=vertices,
            faces=faces,
            material_id=material_id,
            shape_id=shape.attrib.get("id", ""),
            path=mesh_path,
        ))
    return meshes


def _read_ply(path: Path) -> tuple[np.ndarray, np.ndarray]:
    with path.open("rb") as handle:
        header = []
        while True:
            line = handle.readline()
            if not line:
                raise ValueError(f"{path} ended before PLY header finished")
            decoded = line.decode("ascii").strip()
            header.append(decoded)
            if decoded == "end_header":
                break

        if not header or header[0] != "ply":
            raise ValueError(f"{path} is not a PLY file")
        fmt_line = next((line for line in header if line.startswith("format ")), "")
        if "binary_little_endian" in fmt_line:
            endian = "<"
        elif "binary_big_endian" in fmt_line:
            endian = ">"
        else:
            raise ValueError(f"{path} must be binary PLY, got {fmt_line!r}")

        vertex_count = None
        face_count = 0
        vertex_types = []
        face_count_type = "uchar"
        face_index_type = "int"
        in_vertex = False
        in_face = False
        for line in header:
            parts = line.split()
            if parts[:2] == ["element", "vertex"]:
                vertex_count = int(parts[2])
                in_vertex = True
                in_face = False
            elif parts[:2] == ["element", "face"]:
                face_count = int(parts[2])
                in_vertex = False
                in_face = True
            elif parts[:1] == ["element"]:
                in_vertex = False
                in_face = False
            elif in_vertex and len(parts) >= 3 and parts[0] == "property":
                if parts[1] == "list":
                    raise ValueError(f"{path} has list property in vertex element")
                vertex_types.append(parts[1])
            elif in_face and len(parts) >= 5 and parts[:2] == ["property", "list"]:
                face_count_type = parts[2]
                face_index_type = parts[3]

        if vertex_count is None:
            raise ValueError(f"{path} has no vertex element")
        if len(vertex_types) < 3:
            raise ValueError(f"{path} has fewer than three vertex properties")

        vertex_fmt = endian + "".join(_ply_type(t) for t in vertex_types)
        vertex_step = struct.calcsize(vertex_fmt)
        vertices = np.empty((vertex_count, len(vertex_types)), dtype=np.float64)
        for idx in range(vertex_count):
            vertices[idx] = struct.unpack(vertex_fmt, handle.read(vertex_step))

        count_fmt = endian + _ply_type(face_count_type)
        count_step = struct.calcsize(count_fmt)
        index_type = _ply_type(face_index_type)
        index_step = struct.calcsize(endian + index_type)
        faces = []
        for _ in range(face_count):
            count = struct.unpack(count_fmt, handle.read(count_step))[0]
            data = handle.read(int(count) * index_step)
            indices = struct.unpack(endian + index_type * int(count), data)
            for tri_idx in range(1, int(count) - 1):
                faces.append((indices[0], indices[tri_idx], indices[tri_idx + 1]))

    return vertices[:, :3], np.asarray(faces, dtype=np.int64)


def _ply_type(name: str) -> str:
    try:
        return PLY_SCALAR_TYPES[name]
    except KeyError:
        raise ValueError(f"unsupported PLY scalar type {name!r}") from None


def _is_ground(mesh: Mesh) -> bool:
    haystack = f"{mesh.shape_id} {mesh.material_id} {mesh.path.name}".lower()
    return mesh.material_id in GROUND_MATERIALS or "ground" in haystack or "plane" in haystack


def _is_building_roof(mesh: Mesh) -> bool:
    return mesh.material_id in BUILDING_ROOF_MATERIALS


def _is_ignored_occupancy(mesh: Mesh) -> bool:
    return mesh.material_id in IGNORED_OCCUPANCY_MATERIALS


def _optional_triangle_index(meshes) -> TriangleIndex | None:
    mesh_list = [mesh for mesh in meshes if len(mesh.vertices) and len(mesh.faces)]
    if not mesh_list:
        return None
    return _triangle_index(mesh_list)


def _triangle_index(meshes: list[Mesh]) -> TriangleIndex:
    vertices_parts = []
    faces_parts = []
    offset = 0
    for mesh in meshes:
        if len(mesh.vertices) == 0 or len(mesh.faces) == 0:
            continue
        vertices_parts.append(mesh.vertices)
        faces_parts.append(mesh.faces + offset)
        offset += len(mesh.vertices)

    if not vertices_parts or not faces_parts:
        raise ValueError("no triangles available for scene geometry query")

    vertices = np.vstack(vertices_parts)
    faces = np.vstack(faces_parts)
    xy = vertices[faces][:, :, :2]
    return TriangleIndex(
        vertices=vertices,
        faces=faces,
        bbox_min=np.min(xy, axis=1),
        bbox_max=np.max(xy, axis=1),
    )


def _z_at(index: TriangleIndex, x: float, y: float) -> float | None:
    point = np.asarray([x, y], dtype=np.float64)
    mask = (
        (index.bbox_min[:, 0] <= x)
        & (x <= index.bbox_max[:, 0])
        & (index.bbox_min[:, 1] <= y)
        & (y <= index.bbox_max[:, 1])
    )
    values = []
    for face in index.faces[np.flatnonzero(mask)]:
        tri = index.vertices[face]
        bary = _barycentric_2d(point, tri[:, :2])
        if bary is None:
            continue
        w, u, v = bary
        if w >= -1e-7 and u >= -1e-7 and v >= -1e-7:
            values.append(w * tri[0, 2] + u * tri[1, 2] + v * tri[2, 2])
    if not values:
        return None
    return float(max(values))


def _barycentric_2d(point: np.ndarray, tri_xy: np.ndarray) -> tuple[float, float, float] | None:
    a, b, c = tri_xy
    v0 = b - a
    v1 = c - a
    v2 = point - a
    den = v0[0] * v1[1] - v1[0] * v0[1]
    if abs(float(den)) < 1e-9:
        return None
    u = (v2[0] * v1[1] - v1[0] * v2[1]) / den
    v = (v0[0] * v2[1] - v2[0] * v0[1]) / den
    w = 1.0 - u - v
    return float(w), float(u), float(v)
