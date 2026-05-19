#!/usr/bin/env python3
"""
Render two Sionna scenes with identical camera parameters and plot a difference image.

Run in an environment with Sionna RT installed:

    python3 test/plot_scene_render_diff.py \
      data/snu/snu_sionna_terrain.xml data/snu/python_scene_terrain.xml \
      --reference-render data/scene_comparison/snu/blender_terrain_scene.png \
      --candidate-render data/scene_comparison/snu/python_terrain_scene.png \
      --diff-render data/scene_comparison/snu/terrain_render_difference.png
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
from matplotlib import colormaps
import numpy as np
from PIL import Image, ImageDraw, ImageFont


def render_scene(scene_xml: Path, image_path: Path, args: argparse.Namespace) -> None:
    if args.cuda_device is not None:
        os.environ["CUDA_VISIBLE_DEVICES"] = str(args.cuda_device)

    import mitsuba as mi

    mi.set_variant(args.mitsuba_variant)

    from sionna.rt import Camera, load_scene

    scene = load_scene(str(scene_xml), merge_shapes=False)
    scene.frequency = args.frequency
    camera = Camera(position=args.camera, look_at=args.look_at)
    image_path.parent.mkdir(parents=True, exist_ok=True)
    scene.render_to_file(
        camera=camera,
        filename=str(image_path),
        fov=args.fov,
        resolution=args.resolution,
        num_samples=args.samples,
        show_devices=False,
    )


def read_rgb(path: Path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("RGB"), dtype=np.float32)


def write_difference_image(reference: np.ndarray, candidate: np.ndarray, out: Path) -> tuple[float, float, float]:
    delta = candidate - reference
    magnitude = np.sqrt(np.mean(delta * delta, axis=2))
    mae = float(np.mean(np.abs(delta)))
    rmse = float(np.sqrt(np.mean(delta * delta)))
    p95 = float(np.percentile(magnitude, 95))
    vmax = max(1.0, p95)

    heatmap = colormaps["magma"](np.clip(magnitude / vmax, 0.0, 1.0))[..., :3]
    image = Image.fromarray((heatmap * 255.0).astype(np.uint8), mode="RGB").convert("RGBA")
    draw_heatmap_scale(image, vmax, mae, rmse, p95)
    out.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(out)
    return mae, rmse, p95


def draw_heatmap_scale(image: Image.Image, vmax: float, mae: float, rmse: float, p95: float) -> None:
    width, height = image.size
    margin = max(14, width // 70)
    bar_width = min(360, width - 2 * margin)
    bar_height = 16
    text_height = 30
    x0 = margin
    y0 = height - margin - bar_height
    font = ImageFont.load_default()

    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    draw.rectangle(
        [x0 - 8, y0 - text_height, x0 + bar_width + 8, y0 + bar_height + 18],
        fill=(0, 0, 0, 170),
    )

    gradient_values = np.linspace(0.0, 1.0, 256)
    gradient = (colormaps["magma"](gradient_values)[None, :, :3] * 255.0).astype(np.uint8)
    gradient_image = Image.fromarray(gradient, mode="RGB").resize((bar_width, bar_height))
    overlay.paste(gradient_image.convert("RGBA"), (x0, y0))
    draw.rectangle([x0, y0, x0 + bar_width, y0 + bar_height], outline=(255, 255, 255, 220))
    draw.text((x0, y0 - 24), f"RGB RMS diff: 0 to {vmax:.1f} px (p95 clipped)", fill=(255, 255, 255, 255), font=font)
    draw.text((x0, y0 + bar_height + 4), "0", fill=(255, 255, 255, 255), font=font)
    draw.text((x0 + bar_width - 42, y0 + bar_height + 4), f"{vmax:.1f}", fill=(255, 255, 255, 255), font=font)
    draw.text(
        (x0 + bar_width + 18, y0 - 4),
        f"MAE {mae:.1f}\nRMSE {rmse:.1f}",
        fill=(255, 255, 255, 255),
        font=font,
    )
    image.alpha_composite(overlay)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("reference_scene", type=Path)
    parser.add_argument("candidate_scene", type=Path)
    parser.add_argument("--reference-render", type=Path, required=True)
    parser.add_argument("--candidate-render", type=Path, required=True)
    parser.add_argument("--diff-render", type=Path, required=True)
    parser.add_argument("--frequency", type=float, default=3.5e9)
    parser.add_argument("--camera", type=float, nargs=3, default=[0.0, -1200.0, 650.0])
    parser.add_argument("--look-at", type=float, nargs=3, default=[0.0, 0.0, 25.0])
    parser.add_argument("--fov", type=float, default=None)
    parser.add_argument("--resolution", type=int, nargs=2, default=[960, 540])
    parser.add_argument("--samples", type=int, default=16)
    parser.add_argument("--mitsuba-variant", default="cuda_ad_mono")
    parser.add_argument("--cuda-device")
    parser.add_argument("--skip-render", action="store_true")
    args = parser.parse_args()

    if not args.skip_render:
        render_scene(args.reference_scene, args.reference_render, args)
        render_scene(args.candidate_scene, args.candidate_render, args)

    reference = read_rgb(args.reference_render)
    candidate = read_rgb(args.candidate_render)
    if reference.shape != candidate.shape:
        raise ValueError(f"Rendered images have different shapes: {reference.shape} vs {candidate.shape}")

    mae, rmse, p95 = write_difference_image(reference, candidate, args.diff_render)
    print(f"Wrote {args.reference_render}")
    print(f"Wrote {args.candidate_render}")
    print(f"Wrote {args.diff_render}")
    print(f"MAE: {mae:.3f}")
    print(f"RMSE: {rmse:.3f}")
    print(f"RMS p95: {p95:.3f}")


if __name__ == "__main__":
    main()
