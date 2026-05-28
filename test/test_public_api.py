from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import scenebaker


class PublicApiTest(unittest.TestCase):
    def test_load_scene_builds_then_delegates_to_sionna(self) -> None:
        fake_sionna = types.ModuleType("sionna")
        fake_rt = types.ModuleType("sionna.rt")
        loaded = []

        def fake_load_scene(path: str, *, merge_shapes: bool):
            loaded.append((path, merge_shapes))
            return {"path": path, "merge_shapes": merge_shapes}

        fake_rt.load_scene = fake_load_scene

        with tempfile.TemporaryDirectory() as tmp:
            xml_path = Path(tmp) / "python_scene_terrain.xml"
            with patch.dict(sys.modules, {"sionna": fake_sionna, "sionna.rt": fake_rt}):
                with patch("src.cli.build_scene_from_bbox", return_value=xml_path) as build:
                    scene = scenebaker.load_scene(
                        1.0,
                        2.0,
                        3.0,
                        4.0,
                        terrain=True,
                        out_dir=Path(tmp),
                    )

        self.assertEqual(scene, {"path": str(xml_path), "merge_shapes": False})
        self.assertEqual(loaded, [(str(xml_path), False)])
        build.assert_called_once()
        self.assertTrue(build.call_args.kwargs["terrain"])


if __name__ == "__main__":
    unittest.main()
