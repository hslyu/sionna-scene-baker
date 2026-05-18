# Pythonic Sionna Scene Builder

<p align="center">
  <img src="docs/teaser_scene_comparison.png" alt="Side-by-side comparison of a Blender-made reference scene and a Python-made scene" width="100%">
</p>

This repository builds Sionna RT-ready Mitsuba XML scenes directly from OSM
geographic bounds. The main user entry point is:

```bash
python3 build_sionna_scene.py <lat_min> <lat_max> <lon_min> <lon_max> [terrain]
```

The Blender/Blosm/Mitsuba-Blender scenes are reference baselines for paper
comparisons only. They are not the proposed scheme and are not required to use
this repository. Large reference `.blend`, XML, and mesh data can be distributed
separately through Git LFS or an external LFS link.

## Contents

- `build_sionna_scene.py`: Main script for users. Downloads OSM data and builds
  a Sionna-ready scene from a latitude/longitude bounding box.
- `pyScene/`: Python-only OSM to Sionna-ready Mitsuba scene generator.
- `data/`: Place-scoped inputs and generated outputs. User-generated scenes live
  under `data/<place>/`.
- `blender/`: Optional reference-baseline utilities for converting
  Blender/Blosm/Mitsuba-Blender exports. Not needed for normal use.
- `test/`: Test and smoke-test scripts.
- `test/test_sionna_scene_render.py`: Smoke test that loads and renders the
  generated scene with Sionna RT.
- `test/test_sionna_scene_propagation.py`: Smoke test that adds an in-area
  transmitter/receiver pair and computes propagation paths.

## Clone and Pull Data

Files under `data/` are stored with Git LFS. Install Git LFS before cloning:

```bash
git lfs install
git clone git@github.com:hslyu/sionna-scene-baker.git
```

For an existing clone, normal pulls are enough when Git LFS is installed:

```bash
git pull
```

If the repository was cloned before Git LFS was installed, fetch the LFS data
after installing it:

```bash
git lfs install
git lfs pull
```

## Build a Scene

`pyScene` builds a Sionna-ready Mitsuba XML scene directly from OSM data. It
does not require Blender, Blosm, or Mitsuba-Blender.

Install the Python dependencies:

```bash
python3 -m pip install -r requirements.txt
```

Generate a flat scene directly from a latitude/longitude bounding box:

```bash
python3 build_sionna_scene.py \
  <lat_min> <lat_max> <lon_min> <lon_max> \
  --out-dir data/<place>
```

For example, the UT Austin bbox used in this repository is:

```bash
python3 build_sionna_scene.py \
  30.2816 30.2895 -97.7428 -97.7338 \
  --out-dir data/ut
```

Generate a terrain-enabled scene by adding the optional `terrain` argument.
Terrain mode expects SRTM `.hgt` or `.hgt.gz` tiles in `data/<place>/terrain`
unless `--terrain-dir` is provided:

```bash
python3 build_sionna_scene.py \
  30.2816 30.2895 -97.7428 -97.7338 terrain \
  --out-dir data/ut \
  --terrain-dir data/ut/terrain
```

The equivalent package command is:

```bash
python3 -m pyScene.cli bbox \
  <lat_min> <lat_max> <lon_min> <lon_max> [terrain] \
  --out-dir data/<place>
```

If `data/<place>/osm/map.osm` already exists, use `--reuse-osm` to skip the
Overpass download.

The command writes:

- `data/<place>/osm/map.osm`: OSM XML downloaded from Overpass.
- `data/<place>/python_scene.xml`: flat Sionna-ready Mitsuba XML.
- `data/<place>/python_scene_terrain.xml`: terrain-enabled Sionna-ready
  Mitsuba XML.
- `data/<place>/meshes_python*/`: generated PLY meshes for buildings, roads, paths,
  water, vegetation, pedestrian areas, and ground.

To build from an existing OSM extract without downloading from Overpass:

```bash
python3 -m pyScene.cli build \
  --osm data/ut/osm/map.osm \
  --out-xml data/ut/python_scene.xml \
  --out-mesh-dir data/ut/meshes_python
```

`pyScene` currently handles:

- OSM buildings and `building:part` volumes.
- Multipolygon buildings with inner rings.
- `height`, `building:height`, `building:levels`, `min_height`,
  `building:min_height`, and `building:min_level`.
- Simple `roof:height`, `roof:levels`, `roof:shape=gabled`, and
  `roof:shape=hipped` geometry.
- `building=roof` as open canopy roof surfaces, without false blocking walls.
- OSM roads and paths, including `width=*` where available.
- Water, pedestrian areas, and ground.
- Vegetation and forest polygons as shallow 0.5 m foliage volumes by default;
  adjust with `--vegetation-height`.
- Optional SRTM `.hgt` / `.hgt.gz` terrain through `--terrain`.

Without `--terrain`, pyScene generates the same flat scene behavior as before:
a four-vertex ground plane at `z = -0.8`. With `--terrain`, it samples the
terrain tiles under `--terrain-dir`, subtracts the OSM bbox center elevation,
uses the resulting terrain as the ground mesh, and places roads, areas, water,
vegetation, and building bases on that terrain.
Terrain-mode vegetation and forest polygons are clipped to the OSM import
bounds and draped over terrain with subdivided polygon triangles, so large OSM
landuse polygons do not spill far outside the requested scene or disappear
under hilly terrain.

The OSM latitude/longitude values are projected into local scene coordinates in
meters. The projection is centered on the OSM file bounds. For the included UT
Austin extract, the projection center is:

```text
lat0 = 30.28555
lon0 = -97.73830
```

Sionna sees only the generated local `(x, y, z)` coordinates, not the original
latitude/longitude values.

## Optional Reference Baseline

The Blender-derived scenes are optional reference baselines for paper figures,
diagnostics, and sanity checks. They are not part of the Pythonic scene-building
scheme. Users can build scenes with `build_sionna_scene.py` without downloading
or opening any `.blend` file.

If reference data are provided separately through Git LFS, place them under the
corresponding `data/<place>/` directory. The Blender converter can then generate
reference Sionna XMLs for comparison:

```bash
python3 blender/convert_scene_for_sionna.py \
  data/ut/ut_no_terrain.xml \
  data/ut/ut_sionna_no_terrain.xml \
  --mesh-out-dir data/ut/meshes_sionna_no_terrain

python3 blender/convert_scene_for_sionna.py \
  data/ut/ut_terrain.xml \
  data/ut/ut_sionna_terrain.xml \
  --mesh-out-dir data/ut/meshes_sionna_terrain
```

The converter exists to normalize Blender/Mitsuba exports into Sionna-compatible
reference scenes. It is not required for new scene generation.

Compare a generated scene against a Blender-derived reference:

```bash
python3 pyScene/compare.py \
  data/ut/ut_sionna_no_terrain.xml \
  data/ut/python_scene.xml \
  --json data/ut/python_scene_compare.json
```

This comparison is diagnostic. The Blender scene is a useful baseline for
paper-facing comparisons and for finding missing topology, but OSM tags and the
Python implementation are the source of truth for the repository workflow.

## Test Loading and Rendering

Run in an environment with Sionna RT installed:

```bash
python3 test/test_sionna_scene_render.py data/<place>/python_scene.xml --no-preview --render python_scene_render.png
```

For the place-scoped UT scenes:

```bash
python3 test/test_sionna_scene_render.py data/ut/python_scene.xml --no-preview --render ut_python_scene_render.png
python3 test/test_sionna_scene_render.py data/ut/python_scene_terrain.xml --no-preview --render ut_python_scene_terrain_render.png
```

Use interactive preview when a display is available:

```bash
python3 test/test_sionna_scene_render.py data/<place>/python_scene.xml
```

## Test Propagation

Run on a generated scene:

```bash
python3 test/test_sionna_scene_propagation.py data/<place>/python_scene.xml
```

The default transmitter and receiver are placed inside the generated scene
footprint:

- TX: `[-120.0, 120.0, 25.0]`
- RX: `[120.0, 120.0, 1.5]`

The script computes line-of-sight, specular reflection, and refraction paths,
prints delay/gain summaries, and writes `sionna_scene_paths.png`.

For the place-scoped UT scenes:

```bash
python3 test/test_sionna_scene_propagation.py data/ut/python_scene.xml --render ut_python_scene_paths.png
python3 test/test_sionna_scene_propagation.py data/ut/python_scene_terrain.xml --render ut_python_scene_terrain_paths.png
```

For optional randomized reference-vs-candidate propagation checks:

```bash
python3 test/compare_random_propagation.py \
  data/ut/ut_sionna_no_terrain.xml \
  data/ut/python_scene.xml \
  --pairs 25 \
  --seed 11
```

## Blender Reference Preparation

This section is only for producing or updating the optional reference baseline.
If a Blender reference scene still has OSM roads or paths as curves, run this
inside Blender before exporting it to Mitsuba:

```bash
blender your_scene.blend --background --python blender/blender_prepare_sionna_export.py
```

This converts curve/surface/text objects to meshes, triangulates them, and
applies transforms.

## Notes

The material constants for custom road, vegetation, and water surfaces are
starter values. Tune them for the carrier frequency and environment you want to
model.
