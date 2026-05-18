#!/usr/bin/env python3
"""
Smoke-test radio propagation through a generated Sionna RT scene.

Run from the repository root:

    python3 test/test_sionna_scene_propagation.py data/<place>/python_scene.xml
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np


def format_vec(values: list[float]) -> str:
    return "[" + ", ".join(f"{v:.3f}" for v in values) + "]"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("scene_xml", type=Path, nargs="?", default=Path("data/custom/python_scene.xml"))
    parser.add_argument("--frequency", type=float, default=3.5e9)
    parser.add_argument("--tx", type=float, nargs=3, default=[-120.0, 120.0, 25.0])
    parser.add_argument("--rx", type=float, nargs=3, default=[120.0, 120.0, 1.5])
    parser.add_argument("--max-depth", type=int, default=3)
    parser.add_argument("--samples-per-src", type=int, default=100_000)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--render", type=Path, default=Path("sionna_scene_paths.png"))
    args = parser.parse_args()

    from sionna.rt import (
        Camera,
        PathSolver,
        PlanarArray,
        Receiver,
        Transmitter,
        load_scene,
    )

    if not args.scene_xml.exists():
        raise FileNotFoundError(args.scene_xml)

    scene = load_scene(str(args.scene_xml), merge_shapes=False)
    scene.frequency = args.frequency

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

    scene.add(Transmitter("tx", position=args.tx, look_at=args.rx))
    scene.add(Receiver("rx", position=args.rx, look_at=args.tx))

    solver = PathSolver()
    paths = solver(
        scene,
        max_depth=args.max_depth,
        samples_per_src=args.samples_per_src,
        los=True,
        specular_reflection=True,
        diffuse_reflection=False,
        refraction=True,
        diffraction=False,
        seed=args.seed,
    )

    valid = paths.valid.numpy()
    tau = paths.tau.numpy()
    a_real = paths.a[0].numpy()
    a_imag = paths.a[1].numpy()
    coeff = a_real + 1j * a_imag

    valid_count = int(np.count_nonzero(valid))
    print(f"Loaded scene: {args.scene_xml}")
    print(f"Frequency: {args.frequency / 1e9:.3f} GHz")
    print(f"TX position: {format_vec(args.tx)}")
    print(f"RX position: {format_vec(args.rx)}")
    print(f"Objects: {len(scene.objects)}")
    print(f"Radio materials: {len(scene.radio_materials)}")
    print(f"Valid paths: {valid_count}")

    if valid_count == 0:
        raise RuntimeError("No valid propagation paths found")

    flat_valid = valid.reshape(-1)
    flat_tau = tau.reshape(-1)
    flat_coeff = coeff.reshape(-1)

    print("\nPath summary:")
    for i, (is_valid, delay, path_coeff) in enumerate(zip(flat_valid, flat_tau, flat_coeff), start=1):
        if not is_valid:
            continue
        gain_db = 20.0 * np.log10(np.maximum(np.abs(path_coeff), 1e-30))
        print(f"  path {i}: delay={delay * 1e9:.3f} ns, gain={gain_db:.2f} dB")

    cam = Camera(position=[0.0, -1200.0, 650.0], look_at=[0.0, 0.0, 25.0])
    scene.render_to_file(
        camera=cam,
        filename=str(args.render),
        resolution=[1280, 720],
        num_samples=64,
        paths=paths,
        show_devices=True,
        show_orientations=True,
    )
    print(f"\nWrote path render: {args.render}")


if __name__ == "__main__":
    main()
