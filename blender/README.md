# Blender Setup

This directory is for reproducing or updating optional reference-baseline scenes
used in paper comparisons. It is not required for the repository's main
Pythonic scene-building workflow. Users who only want to build Sionna scenes
should use `build_sionna_scene.py` from the repository root.

This directory contains Blender-side helpers for preparing a Blender/OSM scene
for Sionna RT conversion.

## Reference Tutorial

YouTube tutorial for exporting a Sionna scene using Blender:

https://www.youtube.com/watch?v=7xHLDxUaQ7c

## Prerequisites

Download the following:

- Blender `4.2.0` for Linux: `blender-4.2.0-linux-x64.tar.xz`
- Blosm add-on: `blosm_2.7.26.zip`
- Mitsuba-Blender add-on: `mitsuba-blender.zip`, version `0.4.0`

## Install Blender

Extract Blender and launch it:

```bash
tar -xf blender-4.2.0-linux-x64.tar.xz
./blender-4.2.0-linux-x64/blender
```

## Install the Required Add-ons

Inside Blender:

1. Open **Edit -> Preferences -> Add-ons -> Install**.
2. Select the Mitsuba-Blender add-on ZIP and install it.
3. In the Mitsuba-Blender add-on settings, click **Install dependencies using pip**.
4. Install the Blosm add-on ZIP.
5. In the Blosm add-on settings, set the download/cache location.

## Import OSM Data with Blosm

1. Press `N` in the 3D viewport to open the side panel.
2. Open the **Blosm** tab.
3. Use the Blosm web selection workflow:
   - Click **select** to open the map page.
   - Select the area of interest.
   - Click **copy** on the webpage.
   - Paste the copied selection into Blosm.
4. Import the selected OSM data.

## Prepare the Scene

1. Check that buildings, roads, paths, vegetation, and water are present.
2. Add a rectangular ground plane under the imported map.
3. If roads or paths are still Blender curve objects, run:

   ```bash
   blender your_scene.blend --background --python blender/blender_prepare_sionna_export.py
   ```

   This converts curves/surfaces/text objects into triangulated meshes and
   applies object transforms before export.

## Export to Mitsuba

Inside Blender:

1. Open **File -> Export -> Mitsuba**.
2. Export the scene XML and PLY meshes.
3. Place the exported XML in `data/untitled.xml`.
4. Place the exported PLY files in `data/meshes/`.

Then convert the exported reference scene for Sionna RT from the repository root:

```bash
python3 blender/convert_scene_for_sionna.py data/untitled.xml data/sionna_scene.xml
```

Smoke-test the converted reference scene:

```bash
python3 test/test_sionna_scene_render.py data/sionna_scene.xml --no-preview
```
