#!/usr/bin/env python3
"""
Compare propagation results for two Sionna scenes over random TX/RX pairs.

Example:

    python3 test/compare_random_propagation.py \
      data/ut/ut_sionna_no_terrain.xml data/ut/python_scene.xml \
      --pairs 25 --seed 7
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass
class LinkResult:
    valid_paths: int
    strongest_delay_ns: float | None
    strongest_gain_db: float | None


def load_arrays(scene):
    from sionna.rt import PlanarArray

    scene.tx_array = PlanarArray(
        num_rows=1,
        num_cols=1,
        vertical_spacing=0.5,
        horizontal_spacing=0.5,
        pattern="iso",
        polarization="V",
    )
    scene.rx_array = PlanarArray(
        num_rows=1,
        num_cols=1,
        vertical_spacing=0.5,
        horizontal_spacing=0.5,
        pattern="iso",
        polarization="V",
    )


def scene_xy_bounds(scene_xml: Path, material_id: str | None = "mat-ground") -> tuple[float, float, float, float]:
    import struct
    import xml.etree.ElementTree as ET

    root = ET.parse(scene_xml).getroot()
    min_x = min_y = float("inf")
    max_x = max_y = float("-inf")

    shapes = root.findall("./shape")
    if material_id:
        ground_shapes = [
            shape for shape in shapes
            if shape.find("./ref[@name='bsdf']") is not None
            and shape.find("./ref[@name='bsdf']").attrib.get("id") == material_id
        ]
        if ground_shapes:
            shapes = ground_shapes

    for shape in shapes:
        filename = shape.find("./string[@name='filename']")
        if filename is None:
            continue
        path = scene_xml.parent / filename.attrib["value"]
        header = []
        header_size = 0
        with path.open("rb") as f:
            while True:
                line = f.readline()
                if not line:
                    raise ValueError(f"{path} ended before PLY header finished")
                header_size += len(line)
                header.append(line.decode("ascii").strip())
                if line == b"end_header\n":
                    break

            vertex_count = None
            vertex_types = []
            in_vertex = False
            for line in header:
                parts = line.split()
                if parts[:2] == ["element", "vertex"]:
                    vertex_count = int(parts[2])
                    in_vertex = True
                elif parts and parts[0] == "element":
                    in_vertex = False
                elif in_vertex and parts[:1] == ["property"]:
                    vertex_types.append(parts[1])

            if vertex_count is None:
                continue

            formats = {"float": "f", "float32": "f", "double": "d", "float64": "d"}
            fmt = "<" + "".join(formats.get(t, "f") for t in vertex_types)
            step = struct.calcsize(fmt)
            for _ in range(vertex_count):
                values = struct.unpack(fmt, f.read(step))
                min_x = min(min_x, values[0])
                max_x = max(max_x, values[0])
                min_y = min(min_y, values[1])
                max_y = max(max_y, values[1])

    return min_x, max_x, min_y, max_y


def sample_links(
    bounds: tuple[float, float, float, float],
    pairs: int,
    rng: np.random.Generator,
    tx_height: float,
    rx_height: float,
    margin: float,
) -> list[tuple[list[float], list[float]]]:
    min_x, max_x, min_y, max_y = bounds
    min_x += margin
    max_x -= margin
    min_y += margin
    max_y -= margin
    links = []
    for _ in range(pairs):
        tx = [float(rng.uniform(min_x, max_x)), float(rng.uniform(min_y, max_y)), tx_height]
        rx = [float(rng.uniform(min_x, max_x)), float(rng.uniform(min_y, max_y)), rx_height]
        links.append((tx, rx))
    return links


def solve_link(scene, solver, tx_pos, rx_pos, args, solver_seed: int) -> LinkResult:
    from sionna.rt import Receiver, Transmitter

    if "tx" in scene.transmitters:
        scene.remove("tx")
    if "rx" in scene.receivers:
        scene.remove("rx")

    scene.add(Transmitter("tx", position=tx_pos, look_at=rx_pos))
    scene.add(Receiver("rx", position=rx_pos, look_at=tx_pos))

    paths = solver(
        scene,
        max_depth=args.max_depth,
        samples_per_src=args.samples_per_src,
        los=True,
        specular_reflection=True,
        diffuse_reflection=False,
        refraction=True,
        diffraction=False,
        seed=solver_seed,
    )

    valid = paths.valid.numpy().reshape(-1)
    if not np.any(valid):
        return LinkResult(valid_paths=0, strongest_delay_ns=None, strongest_gain_db=None)

    tau = paths.tau.numpy().reshape(-1)
    coeff = (paths.a[0].numpy() + 1j * paths.a[1].numpy()).reshape(-1)
    valid_coeff = coeff[valid]
    strongest = int(np.argmax(np.abs(valid_coeff)))
    valid_tau = tau[valid]
    gain_db = 20.0 * np.log10(max(float(np.abs(valid_coeff[strongest])), 1e-30))
    return LinkResult(
        valid_paths=int(np.count_nonzero(valid)),
        strongest_delay_ns=float(valid_tau[strongest] * 1e9),
        strongest_gain_db=gain_db,
    )


def diff_result(reference: LinkResult, candidate: LinkResult) -> tuple[float | None, float | None]:
    if reference.strongest_delay_ns is None or candidate.strongest_delay_ns is None:
        return None, None
    delay_diff_ns = abs(reference.strongest_delay_ns - candidate.strongest_delay_ns)
    gain_diff_db = abs(reference.strongest_gain_db - candidate.strongest_gain_db)
    return delay_diff_ns, gain_diff_db


def classify_result(
    reference: LinkResult,
    candidate: LinkResult,
    delay_diff_ns: float | None,
    gain_diff_db: float | None,
    args,
) -> str:
    if reference.valid_paths == 0 and candidate.valid_paths == 0:
        return "BOTH_NO_PATH"
    if reference.valid_paths == 0:
        return "REF_NO_PATH"
    if candidate.valid_paths == 0:
        return "CAND_NO_PATH"
    if delay_diff_ns is None or gain_diff_db is None:
        return "UNCOMPARABLE"
    if delay_diff_ns <= args.delay_tolerance_ns and gain_diff_db <= args.gain_tolerance_db:
        return "PASS"
    return "DIFF"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("reference_scene", type=Path)
    parser.add_argument("candidate_scene", type=Path)
    parser.add_argument("--pairs", type=int, default=20)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--frequency", type=float, default=3.5e9)
    parser.add_argument("--max-depth", type=int, default=3)
    parser.add_argument("--samples-per-src", type=int, default=100_000)
    parser.add_argument("--tx-height", type=float, default=25.0)
    parser.add_argument("--rx-height", type=float, default=1.5)
    parser.add_argument("--margin", type=float, default=50.0)
    parser.add_argument("--delay-tolerance-ns", type=float, default=50.0)
    parser.add_argument("--gain-tolerance-db", type=float, default=6.0)
    parser.add_argument("--min-comparable-ratio", type=float, default=0.5)
    args = parser.parse_args()

    from sionna.rt import PathSolver, load_scene

    ref_scene = load_scene(str(args.reference_scene), merge_shapes=False)
    cand_scene = load_scene(str(args.candidate_scene), merge_shapes=False)
    ref_scene.frequency = args.frequency
    cand_scene.frequency = args.frequency
    load_arrays(ref_scene)
    load_arrays(cand_scene)

    rng = np.random.default_rng(args.seed)
    bounds = scene_xy_bounds(args.reference_scene)
    links = sample_links(bounds, args.pairs, rng, args.tx_height, args.rx_height, args.margin)

    solver = PathSolver()
    comparable = 0
    passed = 0
    mismatched = 0
    both_no_path = 0
    delay_diffs = []
    gain_diffs = []

    print(f"Reference: {args.reference_scene}")
    print(f"Candidate: {args.candidate_scene}")
    print(f"Pairs: {args.pairs}")
    print(f"Bounds: x=[{bounds[0]:.1f}, {bounds[1]:.1f}], y=[{bounds[2]:.1f}, {bounds[3]:.1f}]")
    print()

    for i, (tx, rx) in enumerate(links, start=1):
        solver_seed = args.seed + i
        ref = solve_link(ref_scene, solver, tx, rx, args, solver_seed)
        cand = solve_link(cand_scene, solver, tx, rx, args, solver_seed)
        delay_diff_ns, gain_diff_db = diff_result(ref, cand)
        status = classify_result(ref, cand, delay_diff_ns, gain_diff_db, args)

        if status == "BOTH_NO_PATH":
            both_no_path += 1
        elif status in {"REF_NO_PATH", "CAND_NO_PATH", "UNCOMPARABLE", "DIFF"}:
            mismatched += 1

        if status in {"PASS", "DIFF"}:
            comparable += 1
            delay_diffs.append(delay_diff_ns)
            gain_diffs.append(gain_diff_db)
            if status == "PASS":
                passed += 1

        print(
            f"{i:03d} {status} "
            f"tx=[{tx[0]:.1f},{tx[1]:.1f},{tx[2]:.1f}] "
            f"rx=[{rx[0]:.1f},{rx[1]:.1f},{rx[2]:.1f}] "
            f"paths={ref.valid_paths}/{cand.valid_paths} "
            f"delay_diff_ns={delay_diff_ns if delay_diff_ns is not None else 'n/a'} "
            f"gain_diff_db={gain_diff_db if gain_diff_db is not None else 'n/a'}"
        )

    print()
    print(f"Comparable pairs: {comparable}/{args.pairs}")
    print(f"Both no-path pairs: {both_no_path}/{args.pairs}")
    print(f"Mismatched pairs: {mismatched}/{args.pairs}")
    print(f"Passed tolerances: {passed}/{comparable}")
    if delay_diffs:
        print(f"Median delay diff: {np.median(delay_diffs):.3f} ns")
        print(f"Median gain diff: {np.median(gain_diffs):.3f} dB")
        print(f"Max delay diff: {np.max(delay_diffs):.3f} ns")
        print(f"Max gain diff: {np.max(gain_diffs):.3f} dB")

    min_comparable = int(np.ceil(args.pairs * args.min_comparable_ratio))
    if comparable < min_comparable or mismatched or passed != comparable:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
