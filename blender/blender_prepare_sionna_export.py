"""
Run this inside Blender before exporting the Mitsuba XML/PLY scene.

It converts curve/surface/text objects, including OSM road curves, into meshes
and triangulates them so the exported scene can be consumed by Sionna RT.

Blender CLI example:

    blender your_scene.blend --background --python blender/blender_prepare_sionna_export.py
"""

import bpy

CONVERTIBLE_TYPES = {"CURVE", "SURFACE", "FONT"}
EXPORT_NAME_TOKENS = (
    "road",
    "path",
    "street",
    "footway",
    "cycleway",
    "pedestrian",
    "water",
    "vegetation",
    "building",
)


def should_prepare_for_export(obj):
    if obj.type in CONVERTIBLE_TYPES:
        return True
    name = obj.name.lower()
    return any(token in name for token in EXPORT_NAME_TOKENS)


def select_only(obj):
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def prepare_object(obj):
    select_only(obj)

    if obj.type != "MESH":
        bpy.ops.object.convert(target="MESH")

    mesh_obj = bpy.context.view_layer.objects.active
    triangulate = mesh_obj.modifiers.new("sionna_triangulate", "TRIANGULATE")
    triangulate.quad_method = "BEAUTY"
    triangulate.ngon_method = "BEAUTY"
    bpy.ops.object.modifier_apply(modifier=triangulate.name)

    # Exported meshes should own their transforms. This avoids Sionna receiving
    # non-mesh instances or object-level transforms for road/path geometry.
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    return mesh_obj


def main():
    converted = []
    for obj in list(bpy.context.scene.objects):
        if should_prepare_for_export(obj):
            mesh_obj = prepare_object(obj)
            converted.append(mesh_obj.name)

    print(f"Converted/triangulated {len(converted)} objects for Sionna export")
    for name in converted:
        print(f"  {name}")

    if bpy.data.filepath:
        bpy.ops.wm.save_as_mainfile(filepath=bpy.data.filepath)
    else:
        print("Scene was not saved because the .blend file has no filepath")


if __name__ == "__main__":
    main()
