#!/usr/bin/env python3
"""Generate side-by-side render and path-planning artifacts for all scenes."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import numpy as np


PLACES = ("postech", "unist", "snu", "ut")
VARIANTS = {
    "blender_flat": "{place}_sionna_no_terrain.xml",
    "python_flat": "python_scene.xml",
    "blender_terrain": "{place}_sionna_terrain.xml",
    "python_terrain": "python_scene_terrain.xml",
}


def format_gain(value: float | None) -> str:
    return "" if value is None else f"{value:.2f}"


def format_delay(value: float | None) -> str:
    return "" if value is None else f"{value:.3f}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    parser.add_argument("--out-dir", type=Path, default=Path("data/scene_comparison"))
    parser.add_argument("--places", nargs="+", default=list(PLACES))
    parser.add_argument("--variants", nargs="+", choices=sorted(VARIANTS), default=list(VARIANTS))
    parser.add_argument("--frequency", type=float, default=3.5e9)
    parser.add_argument("--tx", type=float, nargs=3, default=[-200.0, 0.0, 160.0])
    parser.add_argument("--rx", type=float, nargs=3, default=[200.0, 0.0, 120.0])
    parser.add_argument("--resolution", type=int, nargs=2, default=[1280, 720])
    parser.add_argument("--samples", type=int, default=64)
    parser.add_argument("--samples-per-src", type=int, default=20_000)
    parser.add_argument("--max-depth", type=int, default=3)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--scene-only", action="store_true")
    parser.add_argument("--index-only", action="store_true")
    args = parser.parse_args()

    if args.index_only:
        rows = [
            json.loads(path.read_text())
            for path in sorted(args.out_dir.glob("*/*_summary.json"))
        ]
        write_outputs(args.out_dir, rows, args)
        print(f"Wrote {args.out_dir / 'summary.json'}")
        print(f"Wrote {args.out_dir / 'summary.csv'}")
        print(f"Wrote {args.out_dir / 'index.html'}")
        return

    from sionna.rt import Camera, PathSolver, PlanarArray, Receiver, Transmitter, load_scene

    args.out_dir.mkdir(parents=True, exist_ok=True)
    solver = PathSolver()
    rows = []

    for place in args.places:
        place_dir = args.out_dir / place
        place_dir.mkdir(parents=True, exist_ok=True)
        for variant in args.variants:
            pattern = VARIANTS[variant]
            scene_xml = args.data_root / place / pattern.format(place=place)
            scene_png = place_dir / f"{variant}_scene.png"
            paths_png = place_dir / f"{variant}_paths.png"
            print(f"[{place}] {variant}: {scene_xml}")

            row = {
                "place": place,
                "variant": variant,
                "scene": str(scene_xml),
                "scene_png": str(scene_png),
                "paths_png": "" if args.scene_only else str(paths_png),
                "status": "ok",
            }
            try:
                scene = load_scene(str(scene_xml), merge_shapes=False)
                scene.frequency = args.frequency
                camera = Camera(position=[0.0, -1200.0, 650.0], look_at=[0.0, 0.0, 25.0])

                scene.render_to_file(
                    camera=camera,
                    filename=str(scene_png),
                    resolution=args.resolution,
                    num_samples=args.samples,
                    show_devices=False,
                )

                row["objects"] = len(scene.objects)
                row["materials"] = len(scene.radio_materials)

                if not args.scene_only:
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
                    valid = paths.valid.numpy().reshape(-1)
                    tau = paths.tau.numpy().reshape(-1)
                    coeff = (paths.a[0].numpy() + 1j * paths.a[1].numpy()).reshape(-1)
                    path_rows = []
                    for i, idx in enumerate(np.flatnonzero(valid), start=1):
                        gain_db = float(20.0 * np.log10(max(abs(coeff[idx]), 1e-30)))
                        path_rows.append({
                            "path": i,
                            "delay_ns": float(tau[idx] * 1e9),
                            "gain_db": gain_db,
                        })

                    row["valid_paths"] = len(path_rows)
                    row["first_delay_ns"] = path_rows[0]["delay_ns"] if path_rows else None
                    row["strongest_gain_db"] = max((p["gain_db"] for p in path_rows), default=None)
                    row["paths"] = path_rows

                    if path_rows:
                        scene.render_to_file(
                            camera=camera,
                            filename=str(paths_png),
                            resolution=args.resolution,
                            num_samples=args.samples,
                            paths=paths,
                            show_devices=True,
                            show_orientations=True,
                        )
            except Exception as exc:
                row["status"] = "error"
                row["error"] = repr(exc)
                print(f"  ERROR: {exc!r}")
            rows.append(row)
            (place_dir / f"{variant}_summary.json").write_text(json.dumps(row, indent=2) + "\n")

    write_outputs(args.out_dir, rows, args)
    print(f"Wrote {args.out_dir / 'summary.json'}")
    print(f"Wrote {args.out_dir / 'summary.csv'}")
    print(f"Wrote {args.out_dir / 'index.html'}")


def write_outputs(out_dir: Path, rows: list[dict], args: argparse.Namespace) -> None:
    (out_dir / "summary.json").write_text(
        json.dumps(
            {
                "tx": args.tx,
                "rx": args.rx,
                "frequency": args.frequency,
                "resolution": args.resolution,
                "samples": args.samples,
                "samples_per_src": args.samples_per_src,
                "max_depth": args.max_depth,
                "seed": args.seed,
                "results": rows,
            },
            indent=2,
        )
        + "\n"
    )

    with (out_dir / "summary.csv").open("w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "place",
                "variant",
                "status",
                "objects",
                "materials",
                "valid_paths",
                "first_delay_ns",
                "strongest_gain_db",
                "scene_png",
                "paths_png",
                "scene",
                "error",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in writer.fieldnames})

    html = [
        "<!doctype html>",
        "<html><head><meta charset='utf-8'>",
        "<title>Scene Comparison</title>",
        "<style>",
        "body{font-family:sans-serif;margin:24px;background:#f7f7f7;color:#222}",
        "h1,h2{margin:16px 0 8px}",
        "table{border-collapse:collapse;width:100%;background:white;margin-bottom:28px}",
        "th,td{border:1px solid #ddd;padding:8px;vertical-align:top}",
        "th{background:#eee;text-align:left}",
        "img{width:100%;max-width:360px;border:1px solid #ccc;background:#111}",
        ".meta{font-size:13px;color:#555}",
        "</style></head><body>",
        "<h1>Scene Comparison</h1>",
        f"<p class='meta'>TX: {args.tx}, RX: {args.rx}, frequency: {args.frequency / 1e9:.3f} GHz</p>",
    ]
    for place in sorted({row["place"] for row in rows}):
        html.append(f"<h2>{place}</h2>")
        html.append("<table>")
        html.append(
            "<tr><th>Variant</th><th>Status</th><th>Paths</th>"
            "<th>First delay ns</th><th>Strongest gain dB</th>"
            "<th>Scene render</th><th>Path render</th></tr>"
        )
        for row in [r for r in rows if r["place"] == place]:
            scene_rel = Path(row["scene_png"]).relative_to(out_dir).as_posix()
            paths_value = row.get("paths_png")
            paths_cell = ""
            if paths_value and Path(paths_value).exists():
                paths_rel = Path(paths_value).relative_to(out_dir).as_posix()
                paths_cell = f"<a href='{paths_rel}'><img src='{paths_rel}'></a>"
            scene_cell = f"<a href='{scene_rel}'><img src='{scene_rel}'></a>" if Path(row["scene_png"]).exists() else ""
            html.append(
                "<tr>"
                f"<td>{row['variant']}</td>"
                f"<td>{row['status']}</td>"
                f"<td>{row.get('valid_paths', '')}</td>"
                f"<td>{format_delay(row.get('first_delay_ns'))}</td>"
                f"<td>{format_gain(row.get('strongest_gain_db'))}</td>"
                f"<td>{scene_cell}</td>"
                f"<td>{paths_cell}</td>"
                "</tr>"
            )
        html.append("</table>")
    html.append("</body></html>")
    (out_dir / "index.html").write_text("\n".join(html) + "\n")


if __name__ == "__main__":
    main()
