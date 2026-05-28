"""Public SionnaSceneBaker API."""

__all__ = [
    "build_scene",
    "build_scene_from_bbox",
    "load_scene",
    "bounds",
    "height",
    "occupied",
]


def __getattr__(name: str):
    if name in {"build_scene", "build_scene_from_bbox", "load_scene"}:
        from src import cli

        return getattr(cli, name)
    if name in {"bounds", "height", "occupied"}:
        from src import scene_utils

        return getattr(scene_utils, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
