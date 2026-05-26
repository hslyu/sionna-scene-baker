"""Geometry generation for Python-only Sionna scenes."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .osm_reader import OsmData, Relation, Way, parse_meters
from .projection import LocalProjection
from .terrain import TerrainModel

try:
    import mapbox_earcut
except ImportError:  # pragma: no cover
    mapbox_earcut = None

try:
    from shapely.geometry import GeometryCollection, LineString, MultiPolygon, Polygon
    from shapely.ops import unary_union
except ImportError:  # pragma: no cover
    GeometryCollection = LineString = MultiPolygon = Polygon = None
    unary_union = None

Point2 = tuple[float, float]
Point3 = tuple[float, float, float]
Face = tuple[int, int, int]
RoadCutout = tuple[Point2, Point2, float, tuple[float, float, float, float]]

ROAD_WIDTHS = {
    "primary": 12.0,
    "primary_link": 8.0,
    "trunk": 12.0,
    "trunk_link": 8.0,
    "secondary": 9.0,
    "secondary_link": 7.0,
    "tertiary": 8.0,
    "tertiary_link": 7.0,
    "residential": 6.0,
    "living_street": 5.0,
    "service": 4.0,
    "unclassified": 6.0,
    "track": 3.0,
    "rest_area": 4.0,
    "construction": 6.0,
    "pedestrian": 5.0,
    "footway": 2.0,
    "bridleway": 2.5,
    "cycleway": 2.5,
    "steps": 2.0,
    "path": 2.0,
}

ROAD_GROUPS = {
    "primary": "roads_primary",
    "primary_link": "roads_primary",
    "trunk": "roads_trunk",
    "trunk_link": "roads_trunk",
    "secondary": "roads_secondary",
    "secondary_link": "roads_secondary",
    "tertiary": "roads_tertiary",
    "tertiary_link": "roads_tertiary",
    "residential": "roads_residential",
    "living_street": "roads_residential",
    "service": "roads_service",
    "unclassified": "roads_unclassified",
    "track": "roads_track",
    "rest_area": "roads_service",
    "construction": "roads_service",
    "pedestrian": "roads_pedestrian",
    "footway": "paths_footway",
    "bridleway": "paths_footway",
    "cycleway": "paths_cycleway",
    "steps": "paths_steps",
    "path": "paths_footway",
}

ROAD_GROUP_PRIORITY = (
    "roads_trunk",
    "roads_primary",
    "roads_secondary",
    "roads_tertiary",
    "roads_residential",
    "roads_unclassified",
    "roads_service",
    "roads_pedestrian",
    "paths_cycleway",
    "paths_footway",
    "paths_steps",
    "roads_track",
)

LEVEL_HEIGHT = 3.0
ONE_LEVEL_BUILDING_AREA = 20.0
HOUSE_AREA = 200.0
DEFAULT_BUILDING_LEVELS = 5
DEFAULT_HOUSE_LEVELS = 2


@dataclass
class Mesh:
    name: str
    vertices: list[Point3] = field(default_factory=list)
    faces: list[Face] = field(default_factory=list)
    _vertex_index: dict[tuple[int, int, int], int] = field(default_factory=dict, init=False)

    def add_vertex(self, point: Point3) -> int:
        key = vertex_key(point)
        if key in self._vertex_index:
            return self._vertex_index[key]
        self.vertices.append(point)
        index = len(self.vertices) - 1
        self._vertex_index[key] = index
        return index

    def add_triangle(self, a: Point3, b: Point3, c: Point3) -> None:
        i = self.add_vertex(a)
        j = self.add_vertex(b)
        k = self.add_vertex(c)
        self.faces.append((i, j, k))

    def extend(self, other: "Mesh") -> None:
        index_map = [self.add_vertex(vertex) for vertex in other.vertices]
        self.faces.extend((index_map[a], index_map[b], index_map[c]) for a, b, c in other.faces)


def vertex_key(point: Point3) -> tuple[int, int, int]:
    return tuple(round(coord * 1_000_000) for coord in point)


def build_scene_meshes(
    osm: OsmData,
    projection: LocalProjection,
    *,
    ground_z: float = -0.8,
    road_z: float = 0.3,
    area_z: float = 0.0,
    water_z: float = 0.2,
    vegetation_z: float = 0.0,
    vegetation_height: float = 0.5,
    min_ground_half_width: float = 700.0,
    terrain: TerrainModel | None = None,
    terrain_grid_size: int = 96,
) -> dict[str, Mesh]:
    meshes = {
        "map_osm_buildings-wall": Mesh("map_osm_buildings-wall"),
        "map_osm_buildings-roof": Mesh("map_osm_buildings-roof"),
        "map_osm_water": Mesh("map_osm_water"),
        "map_osm_forest": Mesh("map_osm_forest"),
        "map_osm_vegetation": Mesh("map_osm_vegetation"),
        "map_osm_areas_pedestrian": Mesh("map_osm_areas_pedestrian"),
        "map_osm_areas_park": Mesh("map_osm_areas_park"),
        "map_osm_areas_steps": Mesh("map_osm_areas_steps"),
        "Plane": Mesh("Plane"),
    }
    for group in set(ROAD_GROUPS.values()):
        meshes[f"map_osm_{group}"] = Mesh(f"map_osm_{group}")

    clip_bounds = osm_clip_bounds(osm, projection)
    ground_bounds = ground_square_bounds(osm, projection, min_ground_half_width)
    road_max_segment_length = None
    if terrain is not None:
        road_max_segment_length = terrain_aligned_max_edge(ground_bounds, terrain_grid_size)
    road_cutouts = road_cutout_segments(osm, projection)
    parent_part_base_heights = building_part_base_heights(osm, projection)
    building_way_ids: set[str] = set()
    road_lines: dict[str, list[tuple[list[Point2], float, bool]]] = {}
    for relation in osm.relations:
        relation_polygons = relation_polygons_with_holes(osm, relation, projection)
        if is_building(relation.tags):
            for polygon, holes in relation_polygons:
                add_building(meshes, polygon, relation.tags, holes, terrain=terrain)
            building_way_ids.update(
                member.ref for member in relation.members
                if member.member_type == "way" and member.role in {"outer", "outline", ""}
            )
        elif is_water(relation.tags):
            for polygon, holes in relation_polygons:
                add_flat_polygon(meshes["map_osm_water"], polygon, water_z, holes, terrain)
        elif is_forest(relation.tags):
            for polygon, holes in relation_polygons:
                add_clipped_vegetation_polygon(
                    meshes["map_osm_forest"],
                    polygon,
                    vegetation_z,
                    vegetation_height,
                    clip_bounds,
                    holes,
                    terrain,
                    ground_bounds,
                    terrain_grid_size,
                    road_cutouts,
                )
        elif is_vegetation(relation.tags):
            for polygon, holes in relation_polygons:
                add_clipped_vegetation_polygon(
                    meshes["map_osm_vegetation"],
                    polygon,
                    vegetation_z,
                    vegetation_height,
                    clip_bounds,
                    holes,
                    terrain,
                    ground_bounds,
                    terrain_grid_size,
                    road_cutouts,
                )
        elif is_park_area(relation.tags):
            for polygon, holes in relation_polygons:
                add_clipped_park_area_polygon(
                    meshes["map_osm_areas_park"],
                    polygon,
                    area_z,
                    clip_bounds,
                    road_cutouts,
                    holes,
                    terrain,
                    ground_bounds,
                    terrain_grid_size,
                )
        elif is_pedestrian_area_tags(relation.tags):
            target = "map_osm_areas_steps" if relation.tags.get("highway") == "steps" else "map_osm_areas_pedestrian"
            for polygon, holes in relation_polygons:
                add_flat_polygon(meshes[target], polygon, area_z, holes, terrain=terrain)

    for way in osm.ways.values():
        points = way_points(osm, way, projection)
        if len(points) < 2:
            continue

        if is_building(way.tags) and way.is_closed and way.way_id not in building_way_ids:
            top_z = parent_part_base_heights.get(way.way_id) if building_base_height(way.tags) == 0.0 else None
            add_building(meshes, clean_ring(points), way.tags, top_z=top_z, terrain=terrain)
        elif is_water(way.tags) and way.is_closed:
            add_flat_polygon(meshes["map_osm_water"], clean_ring(points), water_z, terrain=terrain)
        elif is_forest(way.tags) and way.is_closed:
            add_clipped_vegetation_polygon(
                meshes["map_osm_forest"],
                clean_ring(points),
                vegetation_z,
                vegetation_height,
                clip_bounds,
                terrain=terrain,
                grid_bounds=ground_bounds,
                grid_size=terrain_grid_size,
                road_cutouts=road_cutouts,
            )
        elif is_vegetation(way.tags) and way.is_closed:
            add_clipped_vegetation_polygon(
                meshes["map_osm_vegetation"],
                clean_ring(points),
                vegetation_z,
                vegetation_height,
                clip_bounds,
                terrain=terrain,
                grid_bounds=ground_bounds,
                grid_size=terrain_grid_size,
                road_cutouts=road_cutouts,
            )
        elif is_park_area(way.tags) and way.is_closed:
            add_clipped_park_area_polygon(
                meshes["map_osm_areas_park"],
                clean_ring(points),
                area_z,
                clip_bounds,
                road_cutouts,
                terrain=terrain,
                grid_bounds=ground_bounds,
                grid_size=terrain_grid_size,
            )
        elif is_pedestrian_area(way):
            target = "map_osm_areas_steps" if way.tags.get("highway") == "steps" else "map_osm_areas_pedestrian"
            add_flat_polygon(meshes[target], clean_ring(points), area_z, terrain=terrain)
        elif way.tags.get("highway") in ROAD_GROUPS:
            group = f"map_osm_{ROAD_GROUPS[way.tags['highway']]}"
            width = parse_meters(way.tags.get("width"), ROAD_WIDTHS[way.tags["highway"]])
            road_lines.setdefault(group, []).append((points, width, way.is_closed))

    add_road_surfaces(meshes, road_lines, road_z, terrain, road_max_segment_length)
    if terrain is None:
        add_ground(meshes["Plane"], osm, projection, z=ground_z, min_half_width=min_ground_half_width)
    else:
        add_terrain_ground(
            meshes["Plane"],
            osm,
            projection,
            terrain,
            z=ground_z,
            min_half_width=min_ground_half_width,
            grid_size=terrain_grid_size,
        )
    return {name: mesh for name, mesh in meshes.items() if mesh.faces}


def is_building(tags: dict[str, str]) -> bool:
    return "building" in tags or "building:part" in tags


def is_water(tags: dict[str, str]) -> bool:
    return tags.get("natural") == "water" or "water" in tags


def is_vegetation(tags: dict[str, str]) -> bool:
    return (
        tags.get("natural") in {"tree", "tree_row", "shrubbery", "heath", "grassland", "scrub"}
        or tags.get("landuse") in {"grass", "shrubs", "flowerbed"}
        or tags.get("leisure") == "pitch"
    )


def is_forest(tags: dict[str, str]) -> bool:
    return tags.get("landuse") == "forest" or tags.get("natural") == "wood"


def is_park_area(tags: dict[str, str]) -> bool:
    return tags.get("leisure") in {"park", "garden"}


def is_pedestrian_area(way: Way) -> bool:
    return way.is_closed and is_pedestrian_area_tags(way.tags)


def is_pedestrian_area_tags(tags: dict[str, str]) -> bool:
    return tags.get("highway") in {"pedestrian", "steps"} and tags.get("area") == "yes"


def way_points(osm: OsmData, way: Way, projection: LocalProjection) -> list[Point2]:
    points = []
    for ref in way.node_refs:
        node = osm.nodes.get(ref)
        if node is None:
            continue
        points.append(projection.project(node.lat, node.lon))
    return points


def clean_ring(points: list[Point2]) -> list[Point2]:
    ring = points[:-1] if len(points) > 1 and close(points[0], points[-1]) else points[:]
    cleaned = []
    for point in ring:
        if not cleaned or not close(cleaned[-1], point):
            cleaned.append(point)
    if len(cleaned) > 2 and close(cleaned[0], cleaned[-1]):
        cleaned.pop()
    return cleaned


def close(a: Point2, b: Point2, eps: float = 1e-6) -> bool:
    return abs(a[0] - b[0]) <= eps and abs(a[1] - b[1]) <= eps


def road_cutout_segments(osm: OsmData, projection: LocalProjection) -> list[RoadCutout]:
    cutouts = []
    for way in osm.ways.values():
        highway = way.tags.get("highway")
        if highway not in ROAD_GROUPS:
            continue
        points = way_points(osm, way, projection)
        radius = ROAD_WIDTHS[highway] * 0.5 + 0.5
        for p0, p1 in zip(points, points[1:]):
            if close(p0, p1):
                continue
            min_x = min(p0[0], p1[0]) - radius
            min_y = min(p0[1], p1[1]) - radius
            max_x = max(p0[0], p1[0]) + radius
            max_y = max(p0[1], p1[1]) + radius
            cutouts.append((p0, p1, radius, (min_x, min_y, max_x, max_y)))
    return cutouts


def road_cutout_holes(polygon: list[Point2], road_cutouts: list[RoadCutout]) -> list[list[Point2]]:
    polygon_bounds = bounds_of_points(polygon)
    holes = []
    for p0, p1, radius, cutout_bounds in road_cutouts:
        if not bounds_overlap(polygon_bounds, cutout_bounds):
            continue
        hole = road_cutout_rectangle(p0, p1, radius)
        if all(point_in_polygon(point, polygon) for point in hole):
            holes.append(hole)
    return holes


def road_cutout_rectangle(p0: Point2, p1: Point2, radius: float) -> list[Point2]:
    dx = p1[0] - p0[0]
    dy = p1[1] - p0[1]
    length = math.hypot(dx, dy)
    if length <= 0.0:
        return []
    nx = -dy / length * radius
    ny = dx / length * radius
    return [
        (p0[0] + nx, p0[1] + ny),
        (p1[0] + nx, p1[1] + ny),
        (p1[0] - nx, p1[1] - ny),
        (p0[0] - nx, p0[1] - ny),
    ]


def add_building(
    meshes: dict[str, Mesh],
    polygon: list[Point2],
    tags: dict[str, str],
    holes: list[list[Point2]] | None = None,
    top_z: float | None = None,
    terrain: TerrainModel | None = None,
) -> None:
    if len(polygon) < 3:
        return
    holes = holes or []
    base_z = building_base_height(tags)
    top_z = top_z if top_z is not None else building_height(tags, polygon)
    if top_z <= base_z:
        return
    wall_mesh = meshes["map_osm_buildings-wall"]
    roof_mesh = meshes["map_osm_buildings-roof"]
    ring = ensure_ccw(polygon)

    if tags.get("building") == "roof":
        add_flat_polygon(roof_mesh, ring, top_z, holes, terrain)
        return

    roof_height = building_roof_height(tags)
    roof_shape = tags.get("roof:shape")
    if holes or roof_height <= 0.0 or roof_shape not in {"gabled", "hipped"}:
        add_wall_ring(wall_mesh, ring, base_z, top_z, terrain)
        for hole in holes:
            add_wall_ring(wall_mesh, ensure_cw(clean_ring(hole)), base_z, top_z, terrain)
        add_flat_polygon(roof_mesh, ring, top_z, holes, terrain)
        return

    eave_z = max(base_z, top_z - roof_height)
    if eave_z > base_z:
        add_wall_ring(wall_mesh, ring, base_z, eave_z, terrain)
    add_pyramid_roof(roof_mesh, ring, eave_z, top_z, terrain)


def add_wall_ring(
    mesh: Mesh,
    ring: list[Point2],
    base_z: float,
    top_z: float,
    terrain: TerrainModel | None = None,
) -> None:
    for i, p0 in enumerate(ring):
        p1 = ring[(i + 1) % len(ring)]
        a = point3(p0, base_z, terrain)
        b = point3(p1, base_z, terrain)
        c = point3(p1, top_z, terrain)
        d = point3(p0, top_z, terrain)
        mesh.add_triangle(a, b, c)
        mesh.add_triangle(a, c, d)


def building_height(tags: dict[str, str], polygon: list[Point2] | None = None) -> float:
    height = parse_meters(tags.get("height") or tags.get("building:height"))
    if height:
        return height
    levels = parse_meters(tags.get("building:levels"))
    if levels:
        return level_height(math.ceil(levels))
    return default_building_height(tags, polygon)


def building_base_height(tags: dict[str, str]) -> float:
    min_height = parse_meters(tags.get("min_height") or tags.get("building:min_height"))
    if min_height:
        return min_height
    min_level = parse_meters(tags.get("building:min_level"))
    if min_level:
        return level_height(math.ceil(min_level))
    return 0.0


def building_roof_height(tags: dict[str, str]) -> float:
    height = parse_meters(tags.get("roof:height"))
    if height:
        return height
    levels = parse_meters(tags.get("roof:levels"))
    if levels:
        return level_height(math.ceil(levels))
    return 0.0


def level_height(levels: int) -> float:
    return LEVEL_HEIGHT * levels


def default_building_height(tags: dict[str, str], polygon: list[Point2] | None) -> float:
    building_type = tags.get("building") or tags.get("building:part")
    if building_type in {"house", "detached", "semidetached_house"}:
        return level_height(DEFAULT_HOUSE_LEVELS)
    if polygon is None:
        return level_height(DEFAULT_BUILDING_LEVELS)

    area = abs(signed_area(polygon))
    if area <= ONE_LEVEL_BUILDING_AREA:
        return level_height(1)
    if area <= HOUSE_AREA:
        return level_height(DEFAULT_HOUSE_LEVELS)
    return level_height(DEFAULT_BUILDING_LEVELS)


def building_part_base_heights(osm: OsmData, projection: LocalProjection) -> dict[str, float]:
    parts = []
    for way in osm.ways.values():
        if "building:part" not in way.tags or not way.is_closed:
            continue
        base_z = building_base_height(way.tags)
        if base_z <= 0.0:
            continue
        ring = clean_ring(way_points(osm, way, projection))
        if len(ring) >= 3:
            parts.append((polygon_centroid(ring), base_z))

    parent_heights: dict[str, float] = {}
    for way in osm.ways.values():
        if "building" not in way.tags or "building:part" in way.tags or not way.is_closed:
            continue
        ring = clean_ring(way_points(osm, way, projection))
        if len(ring) < 3:
            continue
        bases = [base_z for centroid, base_z in parts if point_in_polygon(centroid, ring)]
        if bases:
            parent_heights[way.way_id] = min(bases)
    return parent_heights


def add_pyramid_roof(
    mesh: Mesh,
    ring: list[Point2],
    eave_z: float,
    top_z: float,
    terrain: TerrainModel | None = None,
) -> None:
    centroid = polygon_centroid(ring)
    peak = point3(centroid, top_z, terrain)
    for i, p0 in enumerate(ring):
        p1 = ring[(i + 1) % len(ring)]
        mesh.add_triangle(point3(p0, eave_z, terrain), point3(p1, eave_z, terrain), peak)


def add_flat_polygon(
    mesh: Mesh,
    polygon: list[Point2],
    z: float,
    holes: list[list[Point2]] | None = None,
    terrain: TerrainModel | None = None,
) -> None:
    ring = ensure_ccw(clean_ring(polygon))
    clean_holes = [ensure_cw(clean_ring(hole)) for hole in (holes or []) if len(clean_ring(hole)) >= 3]
    for a, b, c in triangulate_polygon(ring, clean_holes):
        mesh.add_triangle(point3(a, z, terrain), point3(b, z, terrain), point3(c, z, terrain))


def add_clipped_park_area_polygon(
    mesh: Mesh,
    polygon: list[Point2],
    z: float,
    bounds: tuple[float, float, float, float],
    road_cutouts: list[RoadCutout],
    holes: list[list[Point2]] | None = None,
    terrain: TerrainModel | None = None,
    grid_bounds: tuple[float, float, float, float] | None = None,
    grid_size: int = 0,
) -> None:
    clipped = clip_polygon_to_bounds(polygon, bounds)
    if len(clipped) < 3:
        return
    kept_holes = [
        clean_ring(hole)
        for hole in (holes or [])
        if len(clean_ring(hole)) >= 3 and all(point_in_bounds(point, bounds) for point in hole)
    ]
    add_park_area_polygon(mesh, clipped, z, road_cutouts, kept_holes, terrain, grid_bounds, grid_size)


def add_park_area_polygon(
    mesh: Mesh,
    polygon: list[Point2],
    z: float,
    road_cutouts: list[RoadCutout],
    holes: list[list[Point2]],
    terrain: TerrainModel | None,
    grid_bounds: tuple[float, float, float, float] | None,
    grid_size: int,
) -> None:
    ring = ensure_ccw(clean_ring(polygon))
    clean_holes = [ensure_cw(clean_ring(hole)) for hole in holes if len(clean_ring(hole)) >= 3]
    polygon_bounds = bounds_of_points(ring)
    cutouts = [
        cutout for cutout in road_cutouts
        if bounds_overlap(polygon_bounds, cutout[3])
    ]
    if not cutouts and terrain is None:
        add_flat_polygon(mesh, ring, z, clean_holes)
        return

    max_edge = park_triangle_max_edge(ring, grid_bounds, grid_size)
    for triangle in triangulate_polygon(ring, clean_holes):
        for a, b, c in subdivide_triangle(*triangle, max_edge=max_edge):
            centroid = triangle_centroid(a, b, c)
            if any(point_near_cutout(centroid, cutout) for cutout in cutouts):
                continue
            mesh.add_triangle(point3(a, z, terrain), point3(b, z, terrain), point3(c, z, terrain))


def park_triangle_max_edge(
    polygon: list[Point2],
    grid_bounds: tuple[float, float, float, float] | None,
    grid_size: int,
) -> float:
    if grid_bounds is None:
        min_x, min_y, max_x, max_y = bounds_of_points(polygon)
    else:
        return terrain_aligned_max_edge(grid_bounds, grid_size)
    return terrain_aligned_max_edge((min_x, min_y, max_x, max_y), grid_size)


def terrain_aligned_max_edge(bounds: tuple[float, float, float, float], grid_size: int) -> float:
    min_x, min_y, max_x, max_y = bounds
    terrain_edge = max(max_x - min_x, max_y - min_y) / max(2, grid_size - 1)
    return min(terrain_edge, 5.0)


def add_vegetation_polygon(
    mesh: Mesh,
    polygon: list[Point2],
    z: float,
    height: float,
    holes: list[list[Point2]] | None = None,
    terrain: TerrainModel | None = None,
) -> None:
    if height <= 0.0:
        add_flat_polygon(mesh, polygon, z, holes, terrain)
        return

    ring = ensure_ccw(clean_ring(polygon))
    clean_holes = [ensure_cw(clean_ring(hole)) for hole in (holes or []) if len(clean_ring(hole)) >= 3]
    top_z = z + height
    for a, b, c in triangulate_polygon(ring, clean_holes):
        mesh.add_triangle(point3(a, top_z, terrain), point3(b, top_z, terrain), point3(c, top_z, terrain))
    add_wall_ring(mesh, ring, z, top_z, terrain)
    for hole in clean_holes:
        add_wall_ring(mesh, hole, z, top_z, terrain)


def add_clipped_vegetation_polygon(
    mesh: Mesh,
    polygon: list[Point2],
    z: float,
    height: float,
    bounds: tuple[float, float, float, float],
    holes: list[list[Point2]] | None = None,
    terrain: TerrainModel | None = None,
    grid_bounds: tuple[float, float, float, float] | None = None,
    grid_size: int = 0,
    road_cutouts: list[RoadCutout] | None = None,
) -> None:
    clipped = clip_polygon_to_bounds(polygon, bounds)
    if len(clipped) < 3:
        return
    kept_holes = [
        clean_ring(hole)
        for hole in (holes or [])
        if len(clean_ring(hole)) >= 3 and all(point_in_bounds(point, bounds) for point in hole)
    ]
    if road_cutouts:
        kept_holes = [*kept_holes, *road_cutout_holes(clipped, road_cutouts)]
    if terrain is not None and grid_bounds is not None:
        add_draped_vegetation_polygon(mesh, clipped, z, height, kept_holes, terrain, grid_bounds, grid_size)
        return
    add_vegetation_polygon(mesh, clipped, z, height, kept_holes, terrain)


def add_draped_vegetation_polygon(
    mesh: Mesh,
    polygon: list[Point2],
    z: float,
    height: float,
    holes: list[list[Point2]],
    terrain: TerrainModel,
    grid_bounds: tuple[float, float, float, float],
    grid_size: int,
) -> None:
    min_x, min_y, max_x, max_y = grid_bounds
    max_edge = max(max_x - min_x, max_y - min_y) / max(2, grid_size - 1)
    top_z = z + height
    for triangle in triangulate_polygon(ensure_ccw(clean_ring(polygon)), holes):
        for a, b, c in subdivide_triangle(*triangle, max_edge=max_edge):
            mesh.add_triangle(point3(a, top_z, terrain), point3(b, top_z, terrain), point3(c, top_z, terrain))
    add_draped_wall_ring(mesh, ensure_ccw(clean_ring(polygon)), z, top_z, terrain, max_edge)
    for hole in holes:
        add_draped_wall_ring(mesh, ensure_cw(clean_ring(hole)), z, top_z, terrain, max_edge)


def subdivide_triangle(
    a: Point2,
    b: Point2,
    c: Point2,
    *,
    max_edge: float,
) -> list[tuple[Point2, Point2, Point2]]:
    triangles = [(a, b, c)]
    for _ in range(12):
        next_triangles = []
        changed = False
        for t0, t1, t2 in triangles:
            if max(edge_length(t0, t1), edge_length(t1, t2), edge_length(t2, t0)) <= max_edge:
                next_triangles.append((t0, t1, t2))
                continue
            m01 = midpoint(t0, t1)
            m12 = midpoint(t1, t2)
            m20 = midpoint(t2, t0)
            next_triangles.extend((
                (t0, m01, m20),
                (m01, t1, m12),
                (m20, m12, t2),
                (m01, m12, m20),
            ))
            changed = True
        triangles = next_triangles
        if not changed:
            break
    return triangles


def add_draped_wall_ring(
    mesh: Mesh,
    ring: list[Point2],
    base_z: float,
    top_z: float,
    terrain: TerrainModel,
    max_edge: float,
) -> None:
    for i, p0 in enumerate(ring):
        p1 = ring[(i + 1) % len(ring)]
        steps = max(1, math.ceil(edge_length(p0, p1) / max_edge))
        points = [lerp_point(p0, p1, step / steps) for step in range(steps + 1)]
        for a, b in zip(points, points[1:]):
            add_wall_segment(mesh, a, b, base_z, top_z, terrain)


def edge_length(a: Point2, b: Point2) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def midpoint(a: Point2, b: Point2) -> Point2:
    return (a[0] + b[0]) * 0.5, (a[1] + b[1]) * 0.5


def lerp_point(a: Point2, b: Point2, t: float) -> Point2:
    return a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t


def add_wall_segment(
    mesh: Mesh,
    p0: Point2,
    p1: Point2,
    base_z: float,
    top_z: float,
    terrain: TerrainModel,
) -> None:
    a = point3(p0, base_z, terrain)
    b = point3(p1, base_z, terrain)
    c = point3(p1, top_z, terrain)
    d = point3(p0, top_z, terrain)
    mesh.add_triangle(a, b, c)
    mesh.add_triangle(a, c, d)


def point3(point: Point2, z: float, terrain: TerrainModel | None = None) -> Point3:
    terrain_z = terrain.elevation_xy(point[0], point[1]) if terrain is not None else 0.0
    return point[0], point[1], terrain_z + z


def triangulate_polygon(
    polygon: list[Point2],
    holes: list[list[Point2]] | None = None,
) -> list[tuple[Point2, Point2, Point2]]:
    """Triangulate a simple polygon without adding vertices.

    Blosm ultimately delegates polygon filling to Blender/BMesh. For the
    Python-only pipeline, ear clipping is the smallest dependency-free
    replacement that preserves concave polygon outlines better than a fan.
    """
    holes = holes or []
    if len(polygon) < 3:
        return []
    if mapbox_earcut is not None:
        triangles = triangulate_with_earcut(polygon, holes)
        if triangles:
            return triangles
    if holes:
        holes = []
    if len(polygon) == 3:
        return [(polygon[0], polygon[1], polygon[2])]

    points = remove_straight_angles(ensure_ccw(polygon))
    indices = list(range(len(points)))
    triangles: list[tuple[Point2, Point2, Point2]] = []

    guard = 0
    while len(indices) > 3 and guard < len(points) * len(points):
        guard += 1
        clipped = False
        for position, current in enumerate(indices):
            previous = indices[position - 1]
            following = indices[(position + 1) % len(indices)]
            a, b, c = points[previous], points[current], points[following]
            if not is_convex(a, b, c):
                continue
            if any(
                point_in_triangle(points[index], a, b, c)
                for index in indices
                if index not in {previous, current, following}
            ):
                continue
            triangles.append((a, b, c))
            del indices[position]
            clipped = True
            break
        if not clipped:
            return triangulate_fan(points)

    if len(indices) == 3:
        triangles.append((points[indices[0]], points[indices[1]], points[indices[2]]))
    return triangles


def triangulate_with_earcut(
    outer: list[Point2],
    holes: list[list[Point2]],
) -> list[tuple[Point2, Point2, Point2]]:
    rings = [ensure_ccw(outer)] + [ensure_cw(hole) for hole in holes]
    vertices = [point for ring in rings for point in ring]
    if len(vertices) < 3:
        return []
    coords = np.asarray(vertices, dtype=np.float64)
    ends = np.cumsum([len(ring) for ring in rings], dtype=np.uint32)
    indices = mapbox_earcut.triangulate_float64(coords, ends)
    return [
        (vertices[int(indices[i])], vertices[int(indices[i + 1])], vertices[int(indices[i + 2])])
        for i in range(0, len(indices), 3)
    ]


def triangulate_fan(polygon: list[Point2]) -> list[tuple[Point2, Point2, Point2]]:
    return [(polygon[0], polygon[i], polygon[i + 1]) for i in range(1, len(polygon) - 1)]


def remove_straight_angles(points: list[Point2], eps: float = 1e-10) -> list[Point2]:
    if len(points) <= 3:
        return points
    cleaned = []
    for i, point in enumerate(points):
        prev_point = points[i - 1]
        next_point = points[(i + 1) % len(points)]
        cross = (
            (point[0] - prev_point[0]) * (next_point[1] - point[1])
            - (point[1] - prev_point[1]) * (next_point[0] - point[0])
        )
        if abs(cross) > eps:
            cleaned.append(point)
    return cleaned if len(cleaned) >= 3 else points


def is_convex(a: Point2, b: Point2, c: Point2, eps: float = 1e-10) -> bool:
    return ((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])) > eps


def point_in_triangle(point: Point2, a: Point2, b: Point2, c: Point2) -> bool:
    if close(point, a) or close(point, b) or close(point, c):
        return False
    area = abs(cross2(a, b, c))
    if area == 0.0:
        return False
    area1 = abs(cross2(point, a, b))
    area2 = abs(cross2(point, b, c))
    area3 = abs(cross2(point, c, a))
    return abs((area1 + area2 + area3) - area) <= max(1e-7, area * 1e-9)


def osm_clip_bounds(osm: OsmData, projection: LocalProjection) -> tuple[float, float, float, float]:
    south, west, north, east = osm.bounds
    corners = [
        projection.project(south, west),
        projection.project(south, east),
        projection.project(north, east),
        projection.project(north, west),
    ]
    return (
        min(point[0] for point in corners),
        min(point[1] for point in corners),
        max(point[0] for point in corners),
        max(point[1] for point in corners),
    )


def ground_square_bounds(
    osm: OsmData,
    projection: LocalProjection,
    min_half_width: float,
) -> tuple[float, float, float, float]:
    min_x, min_y, max_x, max_y = osm_clip_bounds(osm, projection)
    cx = (min_x + max_x) * 0.5
    cy = (min_y + max_y) * 0.5
    half = max(max(max_x - min_x, max_y - min_y) * 0.5 + 250.0, min_half_width)
    return cx - half, cy - half, cx + half, cy + half


def point_in_bounds(point: Point2, bounds: tuple[float, float, float, float]) -> bool:
    min_x, min_y, max_x, max_y = bounds
    return min_x <= point[0] <= max_x and min_y <= point[1] <= max_y


def bounds_of_points(points: list[Point2]) -> tuple[float, float, float, float]:
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return min(xs), min(ys), max(xs), max(ys)


def bounds_overlap(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
) -> bool:
    return a[0] <= b[2] and a[2] >= b[0] and a[1] <= b[3] and a[3] >= b[1]


def triangle_centroid(a: Point2, b: Point2, c: Point2) -> Point2:
    return (a[0] + b[0] + c[0]) / 3.0, (a[1] + b[1] + c[1]) / 3.0


def point_near_cutout(point: Point2, cutout: RoadCutout) -> bool:
    if not point_in_bounds(point, cutout[3]):
        return False
    return point_segment_distance(point, cutout[0], cutout[1]) <= cutout[2]


def point_segment_distance(point: Point2, a: Point2, b: Point2) -> float:
    dx = b[0] - a[0]
    dy = b[1] - a[1]
    length_sq = dx * dx + dy * dy
    if length_sq <= 0.0:
        return math.hypot(point[0] - a[0], point[1] - a[1])
    t = ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / length_sq
    t = min(max(t, 0.0), 1.0)
    closest = a[0] + t * dx, a[1] + t * dy
    return math.hypot(point[0] - closest[0], point[1] - closest[1])


def point_in_polygon(point: Point2, polygon: list[Point2]) -> bool:
    inside = False
    j = len(polygon) - 1
    for i, pi in enumerate(polygon):
        pj = polygon[j]
        if (pi[1] > point[1]) != (pj[1] > point[1]):
            x = (pj[0] - pi[0]) * (point[1] - pi[1]) / (pj[1] - pi[1]) + pi[0]
            if point[0] < x:
                inside = not inside
        j = i
    return inside


def clip_polygon_to_bounds(
    polygon: list[Point2],
    bounds: tuple[float, float, float, float],
) -> list[Point2]:
    min_x, min_y, max_x, max_y = bounds
    points = clean_ring(polygon)
    for inside, intersect in (
        (lambda p: p[0] >= min_x, lambda a, b: intersect_x(a, b, min_x)),
        (lambda p: p[0] <= max_x, lambda a, b: intersect_x(a, b, max_x)),
        (lambda p: p[1] >= min_y, lambda a, b: intersect_y(a, b, min_y)),
        (lambda p: p[1] <= max_y, lambda a, b: intersect_y(a, b, max_y)),
    ):
        points = clip_polygon_edge(points, inside, intersect)
        if len(points) < 3:
            return []
    return clean_ring(points)


def clip_polygon_edge(points, inside, intersect) -> list[Point2]:
    clipped = []
    previous = points[-1]
    previous_inside = inside(previous)
    for current in points:
        current_inside = inside(current)
        if current_inside:
            if not previous_inside:
                clipped.append(intersect(previous, current))
            clipped.append(current)
        elif previous_inside:
            clipped.append(intersect(previous, current))
        previous = current
        previous_inside = current_inside
    return clipped


def intersect_x(a: Point2, b: Point2, x: float) -> Point2:
    if b[0] == a[0]:
        return x, a[1]
    t = (x - a[0]) / (b[0] - a[0])
    return x, a[1] + t * (b[1] - a[1])


def intersect_y(a: Point2, b: Point2, y: float) -> Point2:
    if b[1] == a[1]:
        return a[0], y
    t = (y - a[1]) / (b[1] - a[1])
    return a[0] + t * (b[0] - a[0]), y


def cross2(a: Point2, b: Point2, c: Point2) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def add_road_surfaces(
    meshes: dict[str, Mesh],
    road_lines: dict[str, list[tuple[list[Point2], float, bool]]],
    z: float,
    terrain: TerrainModel | None,
    max_segment_length: float | None,
) -> None:
    if LineString is None or unary_union is None:
        for group, lines in road_lines.items():
            for points, width, closed in lines:
                add_line_strip(
                    meshes[group],
                    points,
                    width=width,
                    z=z,
                    closed=closed,
                    terrain=terrain,
                    max_segment_length=max_segment_length,
                )
        return

    group_geometries = {}
    for group, lines in road_lines.items():
        geometries = []
        for points, width, closed in lines:
            geometry = road_buffer_geometry(points, width, closed, max_segment_length)
            if geometry is not None and not geometry.is_empty:
                geometries.append(geometry)
        if geometries:
            group_geometries[group] = unary_union(geometries)

    accumulated = None
    for group in road_group_order(group_geometries):
        geometry = group_geometries[group]
        if accumulated is not None and not accumulated.is_empty:
            geometry = geometry.difference(accumulated)
        add_shapely_geometry(meshes[group], geometry, z, terrain)
        accumulated = group_geometries[group] if accumulated is None else unary_union([accumulated, group_geometries[group]])


def road_group_order(group_geometries) -> list[str]:
    ordered = [f"map_osm_{group}" for group in ROAD_GROUP_PRIORITY if f"map_osm_{group}" in group_geometries]
    ordered.extend(sorted(group for group in group_geometries if group not in ordered))
    return ordered


def road_buffer_geometry(
    points: list[Point2],
    width: float,
    closed: bool,
    max_segment_length: float | None,
):
    clean = clean_ring(points) if closed else remove_near_duplicates(points)
    if len(clean) < 2:
        return None
    if max_segment_length is not None and max_segment_length > 0.0:
        clean = densify_polyline(clean, closed=closed, max_segment_length=max_segment_length)
    line_points = clean + ([clean[0]] if closed and not close(clean[0], clean[-1]) else [])
    if len(line_points) < 2:
        return None
    return LineString(line_points).buffer(width * 0.5, cap_style=2, join_style=2, mitre_limit=4.0)


def add_shapely_geometry(
    mesh: Mesh,
    geometry,
    z: float,
    terrain: TerrainModel | None,
) -> None:
    for polygon in shapely_polygons(geometry):
        outer = [(float(x), float(y)) for x, y in list(polygon.exterior.coords)[:-1]]
        holes = [
            [(float(x), float(y)) for x, y in list(ring.coords)[:-1]]
            for ring in polygon.interiors
        ]
        if len(outer) >= 3:
            add_buffer_polygon(mesh, outer, holes, z, terrain)


def shapely_polygons(geometry):
    if geometry is None or geometry.is_empty:
        return []
    if Polygon is not None and isinstance(geometry, Polygon):
        return [geometry]
    if MultiPolygon is not None and isinstance(geometry, MultiPolygon):
        return list(geometry.geoms)
    if GeometryCollection is not None and isinstance(geometry, GeometryCollection):
        polygons = []
        for part in geometry.geoms:
            polygons.extend(shapely_polygons(part))
        return polygons
    return []


def add_line_strip(
    mesh: Mesh,
    points: list[Point2],
    *,
    width: float,
    z: float,
    closed: bool = False,
    terrain: TerrainModel | None = None,
    max_segment_length: float | None = None,
) -> None:
    clean = clean_ring(points) if closed else remove_near_duplicates(points)
    if len(clean) < 2:
        return
    if max_segment_length is not None and max_segment_length > 0.0:
        clean = densify_polyline(clean, closed=closed, max_segment_length=max_segment_length)
    add_segment_rectangles(mesh, clean, width * 0.5, z, terrain, max_segment_length, closed)


def add_buffer_polygon(
    mesh: Mesh,
    polygon: list[Point2],
    holes: list[list[Point2]],
    z: float,
    terrain: TerrainModel | None,
) -> None:
    clean_holes = [ensure_cw(clean_ring(hole)) for hole in holes]
    triangles = triangulate_polygon(ensure_ccw(clean_ring(polygon)), clean_holes)
    if not triangles:
        return
    for triangle in triangles:
        a, b, c = triangle
        mesh.add_triangle(point3(a, z, terrain), point3(b, z, terrain), point3(c, z, terrain))


def densify_polyline(points: list[Point2], *, closed: bool, max_segment_length: float) -> list[Point2]:
    densified: list[Point2] = []
    pairs = list(zip(points, points[1:] + ([points[0]] if closed else [])))
    for p0, p1 in pairs:
        if not densified:
            densified.append(p0)
        length = edge_length(p0, p1)
        steps = max(1, math.ceil(length / max_segment_length))
        for step in range(1, steps + 1):
            if closed and p1 == points[0] and step == steps:
                continue
            densified.append(lerp_point(p0, p1, step / steps))
    return remove_near_duplicates(densified)


def remove_near_duplicates(points: list[Point2]) -> list[Point2]:
    cleaned = []
    for point in points:
        if not cleaned or not close(cleaned[-1], point):
            cleaned.append(point)
    if len(cleaned) > 1 and close(cleaned[0], cleaned[-1]):
        cleaned.pop()
    return cleaned


def add_segment_rectangles(
    mesh: Mesh,
    clean: list[Point2],
    half: float,
    z: float,
    terrain: TerrainModel | None,
    max_segment_length: float | None,
    closed: bool,
) -> None:
    segments = zip(clean, clean[1:] + ([clean[0]] if closed else []))
    for p0, p1 in segments:
        dx = p1[0] - p0[0]
        dy = p1[1] - p0[1]
        length = math.hypot(dx, dy)
        if length == 0.0:
            continue
        nx = -dy / length * half
        ny = dx / length * half
        steps = 1
        if max_segment_length is not None and max_segment_length > 0.0:
            steps = max(1, math.ceil(length / max_segment_length))
        for step in range(steps):
            t0 = step / steps
            t1 = (step + 1) / steps
            q0 = (p0[0] + dx * t0, p0[1] + dy * t0)
            q1 = (p0[0] + dx * t1, p0[1] + dy * t1)
            a = point3((q0[0] + nx, q0[1] + ny), z, terrain)
            b = point3((q1[0] + nx, q1[1] + ny), z, terrain)
            c = point3((q1[0] - nx, q1[1] - ny), z, terrain)
            d = point3((q0[0] - nx, q0[1] - ny), z, terrain)
            mesh.add_triangle(a, b, c)
            mesh.add_triangle(a, c, d)


def add_ground(mesh: Mesh, osm: OsmData, projection: LocalProjection, *, z: float, min_half_width: float) -> None:
    south, west, north, east = osm.bounds
    corners = [
        projection.project(south, west),
        projection.project(south, east),
        projection.project(north, east),
        projection.project(north, west),
    ]
    min_x = min(p[0] for p in corners)
    max_x = max(p[0] for p in corners)
    min_y = min(p[1] for p in corners)
    max_y = max(p[1] for p in corners)
    cx = (min_x + max_x) * 0.5
    cy = (min_y + max_y) * 0.5
    half = max(max(max_x - min_x, max_y - min_y) * 0.5 + 250.0, min_half_width)
    a = (cx - half, cy - half, z)
    b = (cx + half, cy - half, z)
    c = (cx + half, cy + half, z)
    d = (cx - half, cy + half, z)
    mesh.add_triangle(a, b, c)
    mesh.add_triangle(a, c, d)


def add_terrain_ground(
    mesh: Mesh,
    osm: OsmData,
    projection: LocalProjection,
    terrain: TerrainModel,
    *,
    z: float,
    min_half_width: float,
    grid_size: int,
) -> None:
    south, west, north, east = osm.bounds
    corners = [
        projection.project(south, west),
        projection.project(south, east),
        projection.project(north, east),
        projection.project(north, west),
    ]
    min_x = min(p[0] for p in corners)
    max_x = max(p[0] for p in corners)
    min_y = min(p[1] for p in corners)
    max_y = max(p[1] for p in corners)
    cx = (min_x + max_x) * 0.5
    cy = (min_y + max_y) * 0.5
    half = max(max(max_x - min_x, max_y - min_y) * 0.5 + 250.0, min_half_width)
    size = max(2, grid_size)
    vertices = []
    for row in range(size):
        y = cy - half + 2.0 * half * row / (size - 1)
        for col in range(size):
            x = cx - half + 2.0 * half * col / (size - 1)
            vertices.append(point3((x, y), z, terrain))
    for row in range(size - 1):
        for col in range(size - 1):
            a = vertices[row * size + col]
            b = vertices[row * size + col + 1]
            c = vertices[(row + 1) * size + col + 1]
            d = vertices[(row + 1) * size + col]
            mesh.add_triangle(a, b, c)
            mesh.add_triangle(a, c, d)


def relation_polygons_with_holes(
    osm: OsmData,
    relation: Relation,
    projection: LocalProjection,
) -> list[tuple[list[Point2], list[list[Point2]]]]:
    outer_rings = []
    inner_rings = []
    outer_fragments = []
    inner_fragments = []
    for member in relation.members:
        if member.member_type != "way":
            continue
        way = osm.ways.get(member.ref)
        if way is None:
            continue
        if member.role == "inner":
            if way.is_closed:
                inner_rings.append(clean_ring(way_points(osm, way, projection)))
            else:
                inner_fragments.append(way.node_refs)
        elif member.role in {"outer", "outline", ""}:
            if way.is_closed:
                outer_rings.append(clean_ring(way_points(osm, way, projection)))
            else:
                outer_fragments.append(way.node_refs)

    for node_refs in stitch_fragments(outer_fragments):
        way = Way("relation", node_refs, relation.tags)
        if way.is_closed:
            outer_rings.append(clean_ring(way_points(osm, way, projection)))
    for node_refs in stitch_fragments(inner_fragments):
        way = Way("relation", node_refs, relation.tags)
        if way.is_closed:
            inner_rings.append(clean_ring(way_points(osm, way, projection)))

    result = [(ring, []) for ring in outer_rings if len(ring) >= 3]
    for hole in inner_rings:
        if len(hole) < 3:
            continue
        centroid = polygon_centroid(hole)
        for outer, holes in result:
            if point_in_polygon(centroid, outer):
                holes.append(hole)
                break
    return result


def polygon_centroid(points: list[Point2]) -> Point2:
    return (
        sum(point[0] for point in points) / len(points),
        sum(point[1] for point in points) / len(points),
    )


def point_in_polygon(point: Point2, polygon: list[Point2]) -> bool:
    x, y = point
    inside = False
    for i, a in enumerate(polygon):
        b = polygon[(i + 1) % len(polygon)]
        if ((a[1] > y) != (b[1] > y)) and (
            x < (b[0] - a[0]) * (y - a[1]) / (b[1] - a[1]) + a[0]
        ):
            inside = not inside
    return inside


def stitch_fragments(fragments: list[list[str]]) -> list[list[str]]:
    remaining = [fragment[:] for fragment in fragments if len(fragment) >= 2]
    rings = []
    while remaining:
        ring = remaining.pop(0)
        changed = True
        while changed and ring[0] != ring[-1]:
            changed = False
            for i, fragment in enumerate(remaining):
                if ring[-1] == fragment[0]:
                    ring.extend(fragment[1:])
                elif ring[-1] == fragment[-1]:
                    ring.extend(reversed(fragment[:-1]))
                elif ring[0] == fragment[-1]:
                    ring = fragment[:-1] + ring
                elif ring[0] == fragment[0]:
                    ring = list(reversed(fragment[1:])) + ring
                else:
                    continue
                remaining.pop(i)
                changed = True
                break
        rings.append(ring)
    return rings


def ensure_ccw(points: list[Point2]) -> list[Point2]:
    if signed_area(points) < 0.0:
        return list(reversed(points))
    return points


def ensure_cw(points: list[Point2]) -> list[Point2]:
    if signed_area(points) > 0.0:
        return list(reversed(points))
    return points


def signed_area(points: list[Point2]) -> float:
    area = 0.0
    for i, p0 in enumerate(points):
        p1 = points[(i + 1) % len(points)]
        area += p0[0] * p1[1] - p1[0] * p0[1]
    return area * 0.5
