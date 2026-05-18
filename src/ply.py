"""PLY mesh writing."""

from __future__ import annotations

import struct
from pathlib import Path

from .geometry import Mesh


def write_binary_ply(path: Path, mesh: Mesh) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "ply\n"
        "format binary_little_endian 1.0\n"
        f"element vertex {len(mesh.vertices)}\n"
        "property float x\n"
        "property float y\n"
        "property float z\n"
        f"element face {len(mesh.faces)}\n"
        "property list uchar int vertex_indices\n"
        "end_header\n"
    ).encode("ascii")

    with path.open("wb") as f:
        f.write(header)
        for vertex in mesh.vertices:
            f.write(struct.pack("<fff", *vertex))
        for face in mesh.faces:
            f.write(struct.pack("<Biii", 3, *face))
