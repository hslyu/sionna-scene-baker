#!/usr/bin/env python3
"""
Convert a Blender/OSM/Mitsuba scene export into a Sionna RT-oriented scene.

What this does:
- Replaces visual Mitsuba diffuse BSDFs with Sionna radio BSDFs.
- Uses built-in Sionna ITU materials where there is a close match.
- Flattens simple `shapegroup` + `instance` PLY objects into transformed PLYs.
- Converts Blender's Y-up coordinates to Sionna's Z-up convention.
- Keeps all scene objects as top-level triangle mesh shapes.

Run from the repository root:

    python3 blender/convert_scene_for_sionna.py data/untitled.xml data/sionna_scene.xml

Then test in Sionna:

    from sionna.rt import load_scene
    scene = load_scene("data/sionna_scene.xml", merge_shapes=False)
"""

from __future__ import annotations

import argparse
import math
import os
import struct
import xml.etree.ElementTree as ET
from pathlib import Path

Matrix4 = list[list[float]]
Vector3 = tuple[float, float, float]


# Starter values. Tune scattering/thickness for your carrier frequency and environment.
# ITU materials use Sionna's built-in frequency-dependent permittivity and
# conductivity models. Custom materials are used only where there is no close
# Sionna ITU material such as vegetation, water, or asphalt-style roads.
MATERIALS = {
    "building_wall": {
        "plugin": "itu-radio-material",
        "itu_type": "concrete",
        "thickness": 0.20,
        "scattering_coefficient": 0.10,
        "xpd_coefficient": 0.00,
    },
    "building_roof": {
        "plugin": "itu-radio-material",
        "itu_type": "concrete",
        "thickness": 0.15,
        "scattering_coefficient": 0.10,
        "xpd_coefficient": 0.00,
    },
    "road": {
        "plugin": "radio-material",
        "relative_permittivity": 3.18,
        "conductivity": 0.003,
        "thickness": 0.05,
        "scattering_coefficient": 0.20,
        "xpd_coefficient": 0.00,
        "color": "0.086500 0.090842 0.088656",
    },
    "vegetation": {
        "plugin": "radio-material",
        "relative_permittivity": 1.50,
        "conductivity": 0.010,
        "thickness": 0.50,
        "scattering_coefficient": 0.60,
        "xpd_coefficient": 0.10,
        "color": "0.007000 0.558000 0.005000",
    },
    "water": {
        "plugin": "radio-material",
        "relative_permittivity": 80.0,
        "conductivity": 0.50,
        "thickness": 0.10,
        "scattering_coefficient": 0.02,
        "xpd_coefficient": 0.00,
        "color": "0.009000 0.002000 0.800000",
    },
    "ground": {
        "plugin": "itu-radio-material",
        "itu_type": "medium_dry_ground",
        "thickness": 0.10,
        "scattering_coefficient": 0.30,
        "xpd_coefficient": 0.00,
    },
}


SCALAR_FORMATS = {
    "char": ("b", 1),
    "int8": ("b", 1),
    "uchar": ("B", 1),
    "uint8": ("B", 1),
    "short": ("h", 2),
    "int16": ("h", 2),
    "ushort": ("H", 2),
    "uint16": ("H", 2),
    "int": ("i", 4),
    "int32": ("i", 4),
    "uint": ("I", 4),
    "uint32": ("I", 4),
    "float": ("f", 4),
    "float32": ("f", 4),
    "double": ("d", 8),
    "float64": ("d", 8),
}

Y_UP_TO_Z_UP = [
    [1.0, 0.0, 0.0, 0.0],
    [0.0, 0.0, -1.0, 0.0],
    [0.0, 1.0, 0.0, 0.0],
    [0.0, 0.0, 0.0, 1.0],
]

IDENTITY_MATRIX = [
    [1.0, 0.0, 0.0, 0.0],
    [0.0, 1.0, 0.0, 0.0],
    [0.0, 0.0, 1.0, 0.0],
    [0.0, 0.0, 0.0, 1.0],
]


def material_for_filename(filename: str) -> str:
    name = Path(filename).name.lower()
    if "water" in name:
        return "water"
    if "vegetation" in name or "forest" in name:
        return "vegetation"
    if "road" in name or "path" in name or "areas_pedestrian" in name or "areas_steps" in name:
        return "road"
    if "roof" in name:
        return "building_roof"
    if "building" in name:
        return "building_wall"
    return "ground"


def parse_matrix(value: str) -> Matrix4:
    values = [float(v) for v in value.split()]
    if len(values) != 16:
        raise ValueError(f"Expected 16 matrix values, got {len(values)}")
    return [values[i : i + 4] for i in range(0, 16, 4)]


def mat_mul(a: Matrix4, b: Matrix4) -> Matrix4:
    return [
        [sum(a[row][k] * b[k][col] for k in range(4)) for col in range(4)]
        for row in range(4)
    ]


def mat_vec_mul(matrix: Matrix4, xyz: Vector3) -> Vector3:
    x, y, z = xyz
    return (
        matrix[0][0] * x + matrix[0][1] * y + matrix[0][2] * z + matrix[0][3],
        matrix[1][0] * x + matrix[1][1] * y + matrix[1][2] * z + matrix[1][3],
        matrix[2][0] * x + matrix[2][1] * y + matrix[2][2] * z + matrix[2][3],
    )


def normalize(v: Vector3) -> Vector3:
    length = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    if length == 0.0:
        return v
    return (v[0] / length, v[1] / length, v[2] / length)


def mat_dir_mul(matrix: Matrix4, xyz: Vector3) -> Vector3:
    x, y, z = xyz
    return normalize((
        matrix[0][0] * x + matrix[0][1] * y + matrix[0][2] * z,
        matrix[1][0] * x + matrix[1][1] * y + matrix[1][2] * z,
        matrix[2][0] * x + matrix[2][1] * y + matrix[2][2] * z,
    ))


def read_header(path: Path) -> tuple[bytes, list[str], int]:
    header = bytearray()
    with path.open("rb") as f:
        while True:
            line = f.readline()
            if not line:
                raise ValueError(f"{path} ended before PLY header finished")
            header.extend(line)
            if line == b"end_header\n":
                break
    return bytes(header), header.decode("ascii").splitlines(), len(header)


def read_ply_bbox(path: Path) -> tuple[Vector3, Vector3]:
    header_bytes, header_lines, header_size = read_header(path)
    if "format binary_little_endian 1.0" not in header_lines:
        raise ValueError(f"{path} is not binary_little_endian PLY")

    vertex_count = None
    vertex_props: list[tuple[str, str]] = []
    in_vertex = False
    for line in header_lines:
        parts = line.split()
        if parts[:2] == ["element", "vertex"]:
            vertex_count = int(parts[2])
            in_vertex = True
        elif parts[:2] == ["element", "face"]:
            in_vertex = False
        elif parts and parts[0] == "element":
            in_vertex = False
        elif in_vertex and parts[:1] == ["property"]:
            if parts[1] == "list":
                raise ValueError(f"{path} has an unsupported list vertex property")
            vertex_props.append((parts[1], parts[2]))

    if vertex_count is None:
        raise ValueError(f"{path} has no vertex element")

    vertex_fmt = "<" + "".join(SCALAR_FORMATS[prop_type][0] for prop_type, _ in vertex_props)
    vertex_size = struct.calcsize(vertex_fmt)
    prop_index = {name: i for i, (_, name) in enumerate(vertex_props)}
    required = {"x", "y", "z"}
    if not required.issubset(prop_index):
        raise ValueError(f"{path} does not have x/y/z vertex properties")

    mins = [math.inf, math.inf, math.inf]
    maxs = [-math.inf, -math.inf, -math.inf]
    with path.open("rb") as f:
        f.seek(header_size)
        for _ in range(vertex_count):
            row = struct.unpack(vertex_fmt, f.read(vertex_size))
            for axis, name in enumerate(("x", "y", "z")):
                value = row[prop_index[name]]
                mins[axis] = min(mins[axis], value)
                maxs[axis] = max(maxs[axis], value)
    return (mins[0], mins[1], mins[2]), (maxs[0], maxs[1], maxs[2])


def detect_axis_mode(source_root: ET.Element, scene_dir: Path) -> str:
    for shape in source_root.findall("./shape"):
        if shape.attrib.get("type") != "ply":
            continue
        mins, maxs = read_ply_bbox(scene_dir / get_ply_filename(shape))
        y_span = maxs[1] - mins[1]
        z_span = maxs[2] - mins[2]
        if z_span > y_span * 2.0 and mins[1] >= -5.0:
            return "y-up"
        return "z-up"
    return "y-up"


def transform_binary_ply(src: Path, dst: Path, matrix: Matrix4, z_offset: float = 0.0) -> None:
    header_bytes, header_lines, header_size = read_header(src)
    if "format binary_little_endian 1.0" not in header_lines:
        raise ValueError(f"{src} is not binary_little_endian PLY")

    vertex_count = None
    vertex_props: list[tuple[str, str]] = []
    in_vertex = False
    for line in header_lines:
        parts = line.split()
        if parts[:2] == ["element", "vertex"]:
            vertex_count = int(parts[2])
            in_vertex = True
        elif parts[:2] == ["element", "face"]:
            in_vertex = False
        elif parts and parts[0] == "element":
            in_vertex = False
        elif in_vertex and parts[:1] == ["property"]:
            if parts[1] == "list":
                raise ValueError(f"{src} has an unsupported list vertex property")
            vertex_props.append((parts[1], parts[2]))

    if vertex_count is None:
        raise ValueError(f"{src} has no vertex element")

    vertex_fmt = "<" + "".join(SCALAR_FORMATS[prop_type][0] for prop_type, _ in vertex_props)
    vertex_size = struct.calcsize(vertex_fmt)
    prop_index = {name: i for i, (_, name) in enumerate(vertex_props)}
    required = {"x", "y", "z"}
    if not required.issubset(prop_index):
        raise ValueError(f"{src} does not have x/y/z vertex properties")

    with src.open("rb") as f:
        f.seek(header_size)
        vertex_blob = f.read(vertex_count * vertex_size)
        rest = f.read()

    transformed = bytearray()
    for i in range(vertex_count):
        row = list(struct.unpack_from(vertex_fmt, vertex_blob, i * vertex_size))
        row[prop_index["x"]], row[prop_index["y"]], row[prop_index["z"]] = mat_vec_mul(
            matrix,
            (row[prop_index["x"]], row[prop_index["y"]], row[prop_index["z"]]),
        )
        row[prop_index["z"]] += z_offset
        if {"nx", "ny", "nz"}.issubset(prop_index):
            row[prop_index["nx"]], row[prop_index["ny"]], row[prop_index["nz"]] = mat_dir_mul(
                matrix,
                (row[prop_index["nx"]], row[prop_index["ny"]], row[prop_index["nz"]]),
            )
        transformed.extend(struct.pack(vertex_fmt, *row))

    dst.parent.mkdir(parents=True, exist_ok=True)
    with dst.open("wb") as f:
        f.write(header_bytes)
        f.write(transformed)
        f.write(rest)


def read_transformed_triangles(src: Path, matrix: Matrix4, z_offset: float = 0.0) -> tuple[list[Vector3], list[tuple[int, int, int]]]:
    header_bytes, header_lines, header_size = read_header(src)
    if "format binary_little_endian 1.0" not in header_lines:
        raise ValueError(f"{src} is not binary_little_endian PLY")

    vertex_count = None
    face_count = None
    vertex_props: list[tuple[str, str]] = []
    in_vertex = False
    for line in header_lines:
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
            if parts[1] == "list":
                raise ValueError(f"{src} has an unsupported list vertex property")
            vertex_props.append((parts[1], parts[2]))

    if vertex_count is None or face_count is None:
        raise ValueError(f"{src} must have vertex and face elements")

    vertex_fmt = "<" + "".join(SCALAR_FORMATS[prop_type][0] for prop_type, _ in vertex_props)
    vertex_size = struct.calcsize(vertex_fmt)
    prop_index = {name: i for i, (_, name) in enumerate(vertex_props)}
    if not {"x", "y", "z"}.issubset(prop_index):
        raise ValueError(f"{src} does not have x/y/z vertex properties")

    vertices: list[Vector3] = []
    faces: list[tuple[int, int, int]] = []
    with src.open("rb") as f:
        f.seek(header_size)
        for _ in range(vertex_count):
            row = struct.unpack(vertex_fmt, f.read(vertex_size))
            x, y, z = mat_vec_mul(matrix, (row[prop_index["x"]], row[prop_index["y"]], row[prop_index["z"]]))
            vertices.append((x, y, z + z_offset))
        for _ in range(face_count):
            count = struct.unpack("<B", f.read(1))[0]
            indices = struct.unpack("<" + "i" * count, f.read(4 * count))
            if count == 3:
                faces.append((indices[0], indices[1], indices[2]))
            elif count > 3:
                faces.extend((indices[0], indices[i], indices[i + 1]) for i in range(1, count - 1))
    return vertices, faces


def write_triangles_ply(dst: Path, vertices: list[Vector3], faces: list[tuple[int, int, int]]) -> None:
    header = (
        "ply\n"
        "format binary_little_endian 1.0\n"
        f"element vertex {len(vertices)}\n"
        "property float x\n"
        "property float y\n"
        "property float z\n"
        f"element face {len(faces)}\n"
        "property list uchar int vertex_indices\n"
        "end_header\n"
    ).encode("ascii")

    dst.parent.mkdir(parents=True, exist_ok=True)
    with dst.open("wb") as f:
        f.write(header)
        for vertex in vertices:
            f.write(struct.pack("<fff", *vertex))
        for face in faces:
            f.write(struct.pack("<Biii", 3, *face))


def transform_vegetation_ply(src: Path, dst: Path, matrix: Matrix4, *, z_offset: float, height: float) -> None:
    if height <= 0.0:
        transform_binary_ply(src, dst, matrix, z_offset=z_offset)
        return

    base_vertices, base_faces = read_transformed_triangles(src, matrix, z_offset=z_offset)
    top_vertices = [(x, y, z + height) for x, y, z in base_vertices]
    faces = [(a + len(base_vertices), b + len(base_vertices), c + len(base_vertices)) for a, b, c in base_faces]

    edge_counts: dict[tuple[int, int], int] = {}
    edge_direction: dict[tuple[int, int], tuple[int, int]] = {}
    for face in base_faces:
        for a, b in ((face[0], face[1]), (face[1], face[2]), (face[2], face[0])):
            key = (min(a, b), max(a, b))
            edge_counts[key] = edge_counts.get(key, 0) + 1
            edge_direction.setdefault(key, (a, b))

    offset = len(base_vertices)
    for key, count in edge_counts.items():
        if count != 1:
            continue
        a, b = edge_direction[key]
        faces.append((a, b, b + offset))
        faces.append((a, b + offset, a + offset))

    write_triangles_ply(dst, base_vertices + top_vertices, faces)


def make_radio_material_xml(material_id: str, params: dict[str, float | str]) -> ET.Element:
    bsdf = ET.Element("bsdf", {"type": str(params["plugin"]), "id": f"mat-{material_id}"})

    if params["plugin"] == "itu-radio-material":
        ET.SubElement(bsdf, "string", {"name": "type", "value": str(params["itu_type"])})
        keys = ("thickness", "scattering_coefficient", "xpd_coefficient")
    else:
        keys = (
            "relative_permittivity",
            "conductivity",
            "thickness",
            "scattering_coefficient",
            "xpd_coefficient",
        )

    for key in keys:
        ET.SubElement(bsdf, "float", {"name": key, "value": str(params[key])})

    # Do not emit the default Lambertian pattern explicitly. Some Sionna
    # versions accept a string for custom radio-material but expect a callable
    # for itu-radio-material, so the safest XML is to rely on the default.
    if "color" in params:
        ET.SubElement(bsdf, "rgb", {"name": "color", "value": str(params["color"])})
    return bsdf


def get_ply_filename(shape: ET.Element) -> str:
    filename = shape.find("./string[@name='filename']")
    if filename is None:
        raise ValueError(f"Shape {shape.attrib.get('id', shape.attrib.get('name'))} has no filename")
    return filename.attrib["value"]


def find_single_ply_child(shape_group: ET.Element) -> ET.Element:
    ply_children = shape_group.findall("./shape[@type='ply']")
    if len(ply_children) != 1:
        group_id = shape_group.attrib.get("id", "<unnamed>")
        raise ValueError(f"Unsupported shapegroup {group_id}: expected exactly one PLY child")
    return ply_children[0]


def get_instance_transform(shape: ET.Element) -> Matrix4:
    matrix_element = shape.find("./transform[@name='to_world']/matrix")
    if matrix_element is None:
        return IDENTITY_MATRIX
    return parse_matrix(matrix_element.attrib["value"])


def mesh_reference_path(mesh_path: Path, scene_xml: Path) -> str:
    return Path(os.path.relpath(mesh_path, start=scene_xml.parent)).as_posix()


def make_shape_xml(shape_id: str, mesh_path: str, material_id: str) -> ET.Element:
    shape = ET.Element("shape", {"type": "ply", "id": shape_id})
    ET.SubElement(shape, "string", {"name": "filename", "value": mesh_path})
    ET.SubElement(shape, "boolean", {"name": "face_normals", "value": "true"})
    ET.SubElement(shape, "ref", {"id": f"mat-{material_id}", "name": "bsdf"})
    return shape


def convert_scene(
    src_xml: Path,
    dst_xml: Path,
    mesh_out_dir: Path,
    axis: str = "auto",
    ground_ply: Path | None = None,
    ground_z_offset: float = 0.0,
    vegetation_clearance: float = 0.0,
    vegetation_height: float = 0.5,
) -> None:
    source_root = ET.parse(src_xml).getroot()
    scene_dir = src_xml.parent
    axis = detect_axis_mode(source_root, scene_dir) if axis == "auto" else axis
    axis_transform = Y_UP_TO_Z_UP if axis == "y-up" else IDENTITY_MATRIX
    dst_xml.parent.mkdir(parents=True, exist_ok=True)
    mesh_out_dir.mkdir(parents=True, exist_ok=True)

    root = ET.Element("scene", {"version": "3.0.0"})
    ET.SubElement(root, "integrator", {"type": "path"})
    for material_id, params in MATERIALS.items():
        root.append(make_radio_material_xml(material_id, params))

    shape_groups = {
        shape.attrib["id"]: shape
        for shape in source_root.findall("./shape")
        if shape.attrib.get("type") == "shapegroup" and "id" in shape.attrib
    }

    emitted_names: set[str] = set()

    def unique_mesh_name(filename: str, shape_id: str) -> str:
        stem = Path(filename).stem
        candidate = f"{stem}_{shape_id}.ply"
        i = 2
        while candidate in emitted_names:
            candidate = f"{stem}_{shape_id}_{i}.ply"
            i += 1
        emitted_names.add(candidate)
        return candidate

    def append_converted_ply(shape_id: str, filename: str, transform: Matrix4) -> None:
        out_path = mesh_out_dir / unique_mesh_name(filename, shape_id)
        material_id = material_for_filename(filename)
        z_offset = vegetation_clearance if material_id == "vegetation" else 0.0
        if material_id == "vegetation":
            transform_vegetation_ply(scene_dir / filename, out_path, transform, z_offset=z_offset, height=vegetation_height)
        else:
            transform_binary_ply(scene_dir / filename, out_path, transform, z_offset=z_offset)
        root.append(make_shape_xml(
            shape_id,
            mesh_reference_path(out_path, dst_xml),
            material_id,
        ))

    for shape in source_root.findall("./shape"):
        shape_type = shape.attrib.get("type")
        shape_id = shape.attrib.get("id") or shape.attrib.get("name") or f"shape_{len(emitted_names)}"

        if shape_type == "shapegroup":
            continue

        if shape_type == "ply":
            append_converted_ply(shape_id, get_ply_filename(shape), axis_transform)
            continue

        if shape_type == "instance":
            ref = shape.find("./ref[@name='shape']")
            if ref is None or ref.attrib.get("id") not in shape_groups:
                raise ValueError(f"Unsupported instance {shape_id}: missing shapegroup reference")
            nested_ply = find_single_ply_child(shape_groups[ref.attrib["id"]])
            filename = get_ply_filename(nested_ply)
            transform = mat_mul(axis_transform, get_instance_transform(shape))
            append_converted_ply(shape_id, filename, transform)
            continue

        raise ValueError(f"Unsupported top-level shape {shape_id}: type={shape_type!r}")

    if ground_ply is not None:
        ground_path = mesh_out_dir / "terrain_ground.ply"
        transform_binary_ply(ground_ply, ground_path, IDENTITY_MATRIX, z_offset=ground_z_offset)
        root.append(make_shape_xml(
            "terrain_ground",
            mesh_reference_path(ground_path, dst_xml),
            "ground",
        ))

    ET.indent(root, space="    ")
    ET.ElementTree(root).write(dst_xml, encoding="utf-8", xml_declaration=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_xml", type=Path)
    parser.add_argument("output_xml", type=Path)
    parser.add_argument("--mesh-out-dir", type=Path)
    parser.add_argument("--axis", choices=("auto", "y-up", "z-up"), default="auto")
    parser.add_argument("--ground-ply", type=Path)
    parser.add_argument("--ground-z-offset", type=float, default=0.0)
    parser.add_argument("--vegetation-clearance", type=float, default=0.0)
    parser.add_argument("--vegetation-height", type=float, default=0.5)
    args = parser.parse_args()

    mesh_out_dir = args.mesh_out_dir
    if mesh_out_dir is None:
        mesh_out_dir = args.output_xml.parent / "meshes_sionna"

    convert_scene(
        args.input_xml,
        args.output_xml,
        mesh_out_dir,
        axis=args.axis,
        ground_ply=args.ground_ply,
        ground_z_offset=args.ground_z_offset,
        vegetation_clearance=args.vegetation_clearance,
        vegetation_height=args.vegetation_height,
    )
    print(f"Wrote {args.output_xml}")
    print(f"Wrote converted meshes to {mesh_out_dir}")


if __name__ == "__main__":
    main()
