from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import scenebaker
from src.geometry import Mesh
from src.mitsuba_xml import write_scene_xml
from src.ply import write_binary_ply


class Scene:
    pass


class SceneUtilsTest(unittest.TestCase):
    def test_bounds_height_and_occupancy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mesh_dir = root / "meshes"
            meshes = {
                "Plane": mesh(
                    "Plane",
                    [(0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 10.0, 10.0), (0.0, 10.0, 10.0)],
                    [(0, 1, 2), (0, 2, 3)],
                ),
                "map_osm_buildings-roof": mesh(
                    "map_osm_buildings-roof",
                    [(4.0, 4.0, 15.0), (6.0, 4.0, 15.0), (6.0, 6.0, 15.0), (4.0, 6.0, 15.0)],
                    [(0, 1, 2), (0, 2, 3)],
                ),
                "map_osm_vegetation": mesh(
                    "map_osm_vegetation",
                    [(1.0, 1.0, 2.0), (2.0, 1.0, 2.0), (2.0, 2.0, 2.0), (1.0, 2.0, 2.0)],
                    [(0, 1, 2), (0, 2, 3)],
                ),
            }
            for name, item in meshes.items():
                write_binary_ply(mesh_dir / f"{name}.ply", item)
            scene_xml = root / "scene.xml"
            write_scene_xml(scene_xml, mesh_dir, meshes)

            scene = Scene()
            scene._scenebaker_scene_xml = scene_xml

            self.assertEqual(scenebaker.bounds(scene), (0.0, 10.0, 0.0, 10.0))
            self.assertAlmostEqual(scenebaker.height(scene, 5.0, 5.0), 5.0)
            self.assertAlmostEqual(scenebaker.height(scene, 5.0, 5.0, buildings=True), 15.0)
            self.assertIsNone(scenebaker.height(scene, 20.0, 20.0))
            self.assertTrue(scenebaker.occupied(scene, 5.0, 5.0))
            self.assertFalse(scenebaker.occupied(scene, 5.0, 5.0, height=20.0))
            self.assertFalse(scenebaker.occupied(scene, 1.5, 1.5))
            self.assertFalse(scenebaker.occupied(scene, 8.0, 8.0))


def mesh(name: str, vertices, faces) -> Mesh:
    return Mesh(name=name, vertices=list(vertices), faces=list(faces))


if __name__ == "__main__":
    unittest.main()
