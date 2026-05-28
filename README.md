# Pythonic Sionna Scene Builder

<table>
  <tr>
    <th>Blender-made reference</th>
    <th>Python-made scene</th>
    <th>Rendered difference</th>
  </tr>
  <tr>
    <td><img src="data/scene_comparison/snu/blender_terrain_scene.png" alt="SNU terrain scene generated from the Blender reference workflow"></td>
    <td><img src="data/scene_comparison/snu/python_terrain_scene.png" alt="SNU terrain scene generated from the Python workflow"></td>
    <td><img src="data/scene_comparison/snu/terrain_render_difference.png" alt="Rendered pixel-level difference between the Blender reference and Python terrain scenes"></td>
  </tr>
</table>

This repository bakes Sionna RT-ready Mitsuba XML scenes directly from OSM
geographic bounds without Blender, Blosm, or Mitsuba-Blender. The main user
entry point is:

```bash
python3 build_sionna_scene.py <lat_min> <lat_max> <lon_min> <lon_max> [terrain]
```

## Usage

```python
import scenebaker

# load_scene(lat_min, lat_max, lon_min, lon_max, terrain=True) -> sionna.rt.Scene
scene = scenebaker.load_scene(30.2816, 30.2895, -97.7428, -97.7338, terrain=True)

x_min, x_max, y_min, y_max = scenebaker.bounds(scene) # scene XY bounds
x, y = 0.5 * (x_min + x_max), 0.5 * (y_min + y_max)

ground_z = scenebaker.height(scene, x, y) # ground height
top_z = scenebaker.height(scene, x, y, buildings=True) # roof or ground height
blocked = scenebaker.occupied(scene, x, y) # true if obstacle
```

`scenebaker.load_scene(...)` returns the same kind of object as
`sionna.rt.load_scene(...)`. Generated XML and meshes are kept in a temporary
directory by default; pass `out_dir` if you want to inspect or reuse them.

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

## Documentation

- [Usage guide](docs/usage.md): scene generation, terrain mode, reference
  baselines, and test scripts.
- [Source package](src/): Python implementation.
- [Blender utilities](blender/): optional reference-baseline conversion tools.

## Citation

If this repository is useful for your work, please cite it 🙏

```bibtex
@software{sionna_scene_baker,
  title = {Pythonic Sionna Scene Builder},
  author = {Lyu, Hyeonsu},
  url = {https://github.com/hslyu/sionna-scene-baker},
  year = {2026}
}
```
