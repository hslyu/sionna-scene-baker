#!/usr/bin/env python3
"""
Smoke-test a converted Sionna RT scene.

Run in an environment with Sionna RT installed:

    python3 test/test_sionna_scene_render.py data/<place>/python_scene.xml

For headless servers, skip the interactive preview:

    python3 test/test_sionna_scene_render.py data/<place>/python_scene.xml --no-preview
"""

from __future__ import annotations

import argparse
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("scene_xml", type=Path, nargs="?", default=Path("data/custom/python_scene.xml"))
    parser.add_argument("--frequency", type=float, default=3.5e9)
    parser.add_argument("--no-preview", action="store_true")
    parser.add_argument("--render", type=Path, default=Path("sionna_scene_render.png"))
    args = parser.parse_args()

    from sionna.rt import (
        Camera,
        load_scene,
    )

    if not args.scene_xml.exists():
        raise FileNotFoundError(args.scene_xml)

    scene = load_scene(str(args.scene_xml), merge_shapes=False)
    scene.frequency = args.frequency

    print(f"Loaded scene: {args.scene_xml}")
    print(f"Frequency: {args.frequency / 1e9:.3f} GHz")
    print(f"Objects: {len(scene.objects)}")
    print(f"Radio materials: {len(scene.radio_materials)}")

    print("\nScene objects:")
    for name, obj in sorted(scene.objects.items()):
        print(f"  {name}: material={obj.radio_material.name}")

    print("\nRadio materials:")
    for name, material in sorted(scene.radio_materials.items()):
        print(f"  {name}: {material}")

    cam = Camera(position=[0.0, -1200.0, 650.0], look_at=[0.0, 0.0, 25.0])

    if not args.no_preview:
        print("\nOpening interactive preview...")
        scene.preview()

    print(f"\nRendering image to {args.render}...")
    scene.render_to_file(
        camera=cam,
        filename=str(args.render),
        resolution=[1280, 720],
        num_samples=64,
        show_devices=False,
    )
    print(f"Wrote {args.render}")


if __name__ == "__main__":
    main()
