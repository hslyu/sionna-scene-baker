"""Command-line interface for Python-only Sionna scene generation."""

from __future__ import annotations

import argparse
import shutil
import struct
import tempfile
import weakref
from pathlib import Path

from .geometry import (
    ROAD_GROUPS,
    Mesh,
    add_terrain_ground,
    build_scene_meshes,
    is_building,
    is_forest,
    is_park_area,
    is_pedestrian_area,
    is_vegetation,
    is_water,
)
from .mitsuba_xml import write_scene_xml
from .osm_reader import read_osm
from .overpass import DEFAULT_OVERPASS_URL, download_osm
from .ply import write_binary_ply
from .projection import projection_from_bounds
from .scene_utils import register
from .terrain import TerrainModel, ensure_hgt_tiles


def build_scene(
    *,
    osm_path: Path,
    out_xml: Path,
    mesh_dir: Path,
    terrain_enabled: bool = False,
    terrain_dir: Path | None = None,
    clean: bool = True,
    ground_z: float = -0.8,
    road_z: float = 0.3,
    area_z: float = 0.0,
    water_z: float = 0.2,
    vegetation_z: float = 0.0,
    vegetation_height: float = 0.5,
    min_ground_half_width: float = 700.0,
    terrain_grid_size: int = 96,
    terrain_vegetation_clearance: float = 0.0,
) -> None:
    osm = read_osm(osm_path)
    projection = projection_from_bounds(*osm.bounds)
    terrain = None
    if terrain_enabled:
        terrain_dir = terrain_dir or osm_path.parent.parent / "terrain"
        south, west, north, east = terrain_download_bounds(osm)
        ensure_hgt_tiles(
            terrain_dir,
            south=south,
            west=west,
            north=north,
            east=east,
            projection=projection,
            margin_m=min_ground_half_width,
        )
        terrain = TerrainModel.from_directory(
            terrain_dir,
            projection,
            center_lat=(osm.bounds[0] + osm.bounds[2]) * 0.5,
            center_lon=(osm.bounds[1] + osm.bounds[3]) * 0.5,
        )
        reference_ground = mesh_dir.parent / "meshes_sionna_terrain" / "terrain_ground.ply"
        if reference_ground.exists():
            terrain.base_elevation = terrain_ground_mean_z(
                osm,
                projection,
                terrain,
                ground_z=ground_z,
                min_ground_half_width=min_ground_half_width,
                grid_size=terrain_grid_size,
            ) - ply_mean_z(reference_ground)
            print(f"Aligned terrain ground height to {reference_ground}")

    if mesh_dir.exists() and clean:
        shutil.rmtree(mesh_dir)
    mesh_dir.mkdir(parents=True, exist_ok=True)

    meshes = build_scene_meshes(
        osm,
        projection,
        ground_z=ground_z,
        road_z=road_z,
        area_z=area_z,
        water_z=water_z,
        vegetation_z=vegetation_z + (terrain_vegetation_clearance if terrain_enabled else 0.0),
        vegetation_height=vegetation_height,
        min_ground_half_width=min_ground_half_width,
        terrain=terrain,
        terrain_grid_size=terrain_grid_size,
    )
    for name, mesh in meshes.items():
        write_binary_ply(mesh_dir / f"{name}.ply", mesh)

    write_scene_xml(out_xml, mesh_dir, meshes)
    print(f"Wrote {out_xml}")
    print(f"Wrote {len(meshes)} meshes to {mesh_dir}")
    for name in sorted(meshes):
        mesh = meshes[name]
        print(f"  {name}: vertices={len(mesh.vertices)} faces={len(mesh.faces)}")


def terrain_ground_mean_z(
    osm,
    projection,
    terrain: TerrainModel,
    *,
    ground_z: float,
    min_ground_half_width: float,
    grid_size: int,
) -> float:
    mesh = Mesh("terrain_ground_probe")
    add_terrain_ground(
        mesh,
        osm,
        projection,
        terrain,
        z=ground_z,
        min_half_width=min_ground_half_width,
        grid_size=grid_size,
    )
    return sum(vertex[2] for vertex in mesh.vertices) / len(mesh.vertices)


def terrain_download_bounds(osm) -> tuple[float, float, float, float]:
    lats = [osm.bounds[0], osm.bounds[2]]
    lons = [osm.bounds[1], osm.bounds[3]]

    def add_way_nodes(way) -> None:
        for ref in way.node_refs:
            node = osm.nodes.get(ref)
            if node is not None:
                lats.append(node.lat)
                lons.append(node.lon)

    for relation in osm.relations:
        if (
            is_building(relation.tags)
            or is_water(relation.tags)
            or is_forest(relation.tags)
            or is_vegetation(relation.tags)
            or is_park_area(relation.tags)
        ):
            for member in relation.members:
                if member.member_type == "way":
                    way = osm.ways.get(member.ref)
                    if way is not None:
                        add_way_nodes(way)

    for way in osm.ways.values():
        if (
            is_building(way.tags)
            or is_water(way.tags)
            or is_forest(way.tags)
            or is_vegetation(way.tags)
            or is_park_area(way.tags)
            or is_pedestrian_area(way)
            or way.tags.get("highway") in ROAD_GROUPS
        ):
            add_way_nodes(way)

    return min(lats), min(lons), max(lats), max(lons)


def ply_mean_z(path: Path) -> float:
    with path.open("rb") as f:
        header = []
        while True:
            line = f.readline()
            if not line:
                raise ValueError(f"{path} ended before PLY header finished")
            text = line.decode("ascii").strip()
            header.append(text)
            if text == "end_header":
                break

        vertex_count = 0
        vertex_types = []
        in_vertex = False
        for line in header:
            parts = line.split()
            if parts[:2] == ["element", "vertex"]:
                vertex_count = int(parts[2])
                in_vertex = True
            elif parts and parts[0] == "element":
                in_vertex = False
            elif in_vertex and parts[:1] == ["property"]:
                vertex_types.append(parts[1])

        formats = {"float": "f", "float32": "f", "double": "d", "float64": "d"}
        fmt = "<" + "".join(formats.get(t, "f") for t in vertex_types)
        step = struct.calcsize(fmt)
        total_z = 0.0
        for _ in range(vertex_count):
            total_z += struct.unpack(fmt, f.read(step))[2]
    return total_z / vertex_count


def build(args: argparse.Namespace) -> None:
    build_scene(
        osm_path=args.osm,
        out_xml=args.out_xml,
        mesh_dir=args.out_mesh_dir,
        terrain_enabled=args.terrain,
        terrain_dir=args.terrain_dir,
        clean=args.clean,
        ground_z=args.ground_z,
        road_z=args.road_z,
        area_z=args.area_z,
        water_z=args.water_z,
        vegetation_z=args.vegetation_z,
        vegetation_height=args.vegetation_height,
        min_ground_half_width=args.min_ground_half_width,
        terrain_grid_size=args.terrain_grid_size,
        terrain_vegetation_clearance=args.terrain_vegetation_clearance,
    )


def bbox(args: argparse.Namespace) -> None:
    build_scene_from_bbox(
        args.lat_min,
        args.lat_max,
        args.lon_min,
        args.lon_max,
        terrain=args.terrain or args.terrain_mode == "terrain",
        out_dir=args.out_dir,
        reuse_osm=args.reuse_osm,
        overpass_url=args.overpass_url,
        overpass_timeout=args.overpass_timeout,
        terrain_dir=args.terrain_dir,
        clean=args.clean,
        ground_z=args.ground_z,
        road_z=args.road_z,
        area_z=args.area_z,
        water_z=args.water_z,
        vegetation_z=args.vegetation_z,
        vegetation_height=args.vegetation_height,
        min_ground_half_width=args.min_ground_half_width,
        terrain_grid_size=args.terrain_grid_size,
        terrain_vegetation_clearance=args.terrain_vegetation_clearance,
    )


def build_scene_from_bbox(
    lat_min: float,
    lat_max: float,
    lon_min: float,
    lon_max: float,
    *,
    terrain: bool | str = False,
    out_dir: Path = Path("data/custom"),
    reuse_osm: bool = False,
    overpass_url: str = DEFAULT_OVERPASS_URL,
    overpass_timeout: int = 180,
    terrain_dir: Path | None = None,
    clean: bool = True,
    ground_z: float = -0.8,
    road_z: float = 0.3,
    area_z: float = 0.0,
    water_z: float = 0.2,
    vegetation_z: float = 0.0,
    vegetation_height: float = 0.5,
    min_ground_half_width: float = 700.0,
    terrain_grid_size: int = 96,
    terrain_vegetation_clearance: float = 0.0,
) -> Path:
    south, north = sorted((lat_min, lat_max))
    west, east = sorted((lon_min, lon_max))
    terrain_enabled = terrain == "terrain" or terrain is True
    out_dir = Path(out_dir)
    osm_path = out_dir / "osm" / "map.osm"
    mesh_dir = out_dir / ("meshes_python_terrain" if terrain_enabled else "meshes_python")
    out_xml = out_dir / ("python_scene_terrain.xml" if terrain_enabled else "python_scene.xml")
    terrain_dir = Path(terrain_dir) if terrain_dir is not None else out_dir / "terrain"

    if not reuse_osm or not osm_path.exists():
        print(f"Downloading OSM to {osm_path}")
        download_osm(
            osm_path,
            south=south,
            west=west,
            north=north,
            east=east,
            overpass_url=overpass_url,
            timeout=overpass_timeout,
        )
    else:
        print(f"Using existing OSM: {osm_path}")

    build_scene(
        osm_path=osm_path,
        out_xml=out_xml,
        mesh_dir=mesh_dir,
        terrain_enabled=terrain_enabled,
        terrain_dir=terrain_dir,
        clean=clean,
        ground_z=ground_z,
        road_z=road_z,
        area_z=area_z,
        water_z=water_z,
        vegetation_z=vegetation_z,
        vegetation_height=vegetation_height,
        min_ground_half_width=min_ground_half_width,
        terrain_grid_size=terrain_grid_size,
        terrain_vegetation_clearance=terrain_vegetation_clearance,
    )
    return out_xml


def load_scene(
    lat_min: float,
    lat_max: float,
    lon_min: float,
    lon_max: float,
    terrain: bool | str = False,
    *,
    out_dir: Path | None = None,
    reuse_osm: bool = False,
    overpass_url: str = DEFAULT_OVERPASS_URL,
    overpass_timeout: int = 180,
    merge_shapes: bool = False,
    terrain_dir: Path | None = None,
    clean: bool = True,
    ground_z: float = -0.8,
    road_z: float = 0.3,
    area_z: float = 0.0,
    water_z: float = 0.2,
    vegetation_z: float = 0.0,
    vegetation_height: float = 0.5,
    min_ground_half_width: float = 700.0,
    terrain_grid_size: int = 96,
    terrain_vegetation_clearance: float = 0.0,
):
    try:
        from sionna.rt import load_scene as sionna_load_scene
    except ImportError as exc:  # pragma: no cover - depends on optional Sionna install
        raise ImportError("scenebaker.load_scene requires sionna to be installed") from exc

    temp_out_dir = None
    if out_dir is None:
        temp_out_dir = Path(tempfile.mkdtemp(prefix="sionna-scene-baker-"))
        out_dir = temp_out_dir

    try:
        scene_xml = build_scene_from_bbox(
            lat_min,
            lat_max,
            lon_min,
            lon_max,
            terrain=terrain,
            out_dir=out_dir,
            reuse_osm=reuse_osm,
            overpass_url=overpass_url,
            overpass_timeout=overpass_timeout,
            terrain_dir=terrain_dir,
            clean=clean,
            ground_z=ground_z,
            road_z=road_z,
            area_z=area_z,
            water_z=water_z,
            vegetation_z=vegetation_z,
            vegetation_height=vegetation_height,
            min_ground_half_width=min_ground_half_width,
            terrain_grid_size=terrain_grid_size,
            terrain_vegetation_clearance=terrain_vegetation_clearance,
        )
        scene = sionna_load_scene(str(scene_xml), merge_shapes=merge_shapes)
        register(scene, scene_xml)
    except Exception:
        if temp_out_dir is not None:
            shutil.rmtree(temp_out_dir, ignore_errors=True)
        raise
    if temp_out_dir is not None:
        _cleanup_with_scene(scene, temp_out_dir)
    return scene


def _cleanup_with_scene(scene, path: Path) -> None:
    try:
        weakref.finalize(scene, shutil.rmtree, path, ignore_errors=True)
    except TypeError:
        try:
            scene._scenebaker_temp_dir = _TempSceneFiles(path)
        except AttributeError:
            pass


class _TempSceneFiles:
    def __init__(self, path: Path) -> None:
        self.path = path

    def __del__(self) -> None:
        try:
            shutil.rmtree(self.path, ignore_errors=True)
        except Exception:
            pass


def add_build_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--ground-z", type=float, default=-0.8)
    parser.add_argument("--road-z", type=float, default=0.3)
    parser.add_argument("--area-z", type=float, default=0.0)
    parser.add_argument("--water-z", type=float, default=0.2)
    parser.add_argument("--vegetation-z", type=float, default=0.0)
    parser.add_argument("--vegetation-height", type=float, default=0.5)
    parser.add_argument("--min-ground-half-width", type=float, default=700.0)
    parser.add_argument("--terrain", action="store_true")
    parser.add_argument("--terrain-dir", type=Path)
    parser.add_argument("--terrain-grid-size", type=int, default=96)
    parser.add_argument("--terrain-vegetation-clearance", type=float, default=0.0)
    parser.add_argument("--no-clean", action="store_false", dest="clean")
    parser.set_defaults(clean=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_parser = subparsers.add_parser("build")
    build_parser.add_argument("--osm", type=Path, default=Path("data/osm/map.osm"))
    build_parser.add_argument("--out-xml", type=Path, default=Path("data/python_scene.xml"))
    build_parser.add_argument("--out-mesh-dir", type=Path, default=Path("data/meshes_python"))
    add_build_options(build_parser)
    build_parser.set_defaults(func=build)

    bbox_parser = subparsers.add_parser("bbox")
    bbox_parser.add_argument("lat_min", type=float)
    bbox_parser.add_argument("lat_max", type=float)
    bbox_parser.add_argument("lon_min", type=float)
    bbox_parser.add_argument("lon_max", type=float)
    bbox_parser.add_argument("terrain_mode", nargs="?", choices=("terrain",))
    bbox_parser.add_argument("--out-dir", type=Path, default=Path("data/custom"))
    bbox_parser.add_argument("--reuse-osm", action="store_true")
    bbox_parser.add_argument("--overpass-url", default=DEFAULT_OVERPASS_URL)
    bbox_parser.add_argument("--overpass-timeout", type=int, default=180)
    add_build_options(bbox_parser)
    bbox_parser.set_defaults(func=bbox)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
