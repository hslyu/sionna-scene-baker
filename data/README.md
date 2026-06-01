# Data Layout

This directory contains the scene assets used by examples, tests, and paper
figures.

## Scene Bounds

Bounding boxes use `lat_min, lat_max, lon_min, lon_max`, matching
`scenebaker.load_scene(...)` and `build_sionna_scene.py`.

| Site | lat_min | lat_max | lon_min | lon_max |
|---|---:|---:|---:|---:|
| POSTECH | 36.0083504 | 36.0160599 | 129.3181458 | 129.3280487 |
| SNU | 37.4617805 | 37.4692001 | 126.9479675 | 126.9574127 |
| UNIST | 35.5703011 | 35.5777016 | 129.1850433 | 129.1938782 |
| UT Austin | 30.2816010 | 30.2894993 | -97.7427979 | -97.7338028 |

These values are recorded in each `data/<site>/osm/map.osm` file's
`<bounds ...>` tag.

## Scene XML Files

Current Python-generated scenes are stored as:

- `data/<site>/<site>_scenebaker_no_terrain.xml`: flat SionnaSceneBaker scene.
- `data/<site>/<site>_scenebaker_terrain.xml`: terrain-enabled SionnaSceneBaker scene.
- `data/<site>/meshes_python*/`: meshes used by the Python-generated scenes.

Blender-reference scenes are stored as:

- `data/<site>/<site>_raw_blender_no_terrain.xml`: raw Blender/Mitsuba export.
- `data/<site>/<site>_raw_blender_terrain.xml`: raw Blender/Mitsuba terrain export.
- `data/<site>/<site>_blender_no_terrain.xml`: Blender export converted for Sionna RT.
- `data/<site>/<site>_blender_terrain.xml`: Blender terrain export converted for Sionna RT.
- `data/<site>/meshes_sionna*/`: meshes used by the converted Blender-reference scenes.

For new SionnaSceneBaker outputs, use `<site>_scenebaker_*.xml`. The
`<site>_blender_*.xml` files are retained as Blender-generated baselines for
comparisons, and `<site>_raw_blender_*.xml` files are pre-conversion Blender
exports.
