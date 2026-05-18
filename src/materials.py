"""Sionna radio material XML definitions."""

from __future__ import annotations

import xml.etree.ElementTree as ET


MATERIALS = {
    "building_wall": {
        "plugin": "itu-radio-material",
        "itu_type": "concrete",
        "thickness": 0.20,
        "scattering_coefficient": 0.10,
        "xpd_coefficient": 0.00,
    },
    "building_roof": {
        "plugin": "itu-radio-material",
        "itu_type": "concrete",
        "thickness": 0.15,
        "scattering_coefficient": 0.10,
        "xpd_coefficient": 0.00,
    },
    "road": {
        "plugin": "radio-material",
        "relative_permittivity": 3.18,
        "conductivity": 0.003,
        "thickness": 0.05,
        "scattering_coefficient": 0.20,
        "xpd_coefficient": 0.00,
        "color": "0.086500 0.090842 0.088656",
    },
    "vegetation": {
        "plugin": "radio-material",
        "relative_permittivity": 1.50,
        "conductivity": 0.010,
        "thickness": 0.50,
        "scattering_coefficient": 0.60,
        "xpd_coefficient": 0.10,
        "color": "0.007000 0.558000 0.005000",
    },
    "water": {
        "plugin": "radio-material",
        "relative_permittivity": 80.0,
        "conductivity": 0.50,
        "thickness": 0.10,
        "scattering_coefficient": 0.02,
        "xpd_coefficient": 0.00,
        "color": "0.009000 0.002000 0.800000",
    },
    "ground": {
        "plugin": "itu-radio-material",
        "itu_type": "medium_dry_ground",
        "thickness": 0.10,
        "scattering_coefficient": 0.30,
        "xpd_coefficient": 0.00,
    },
}


def material_for_mesh(mesh_name: str) -> str:
    if "water" in mesh_name:
        return "water"
    if "vegetation" in mesh_name or "forest" in mesh_name:
        return "vegetation"
    if "road" in mesh_name or "path" in mesh_name or "areas_" in mesh_name:
        return "road"
    if "roof" in mesh_name:
        return "building_roof"
    if "building" in mesh_name:
        return "building_wall"
    return "ground"


def make_material_xml(material_id: str, params: dict[str, float | str]) -> ET.Element:
    bsdf = ET.Element("bsdf", {"type": str(params["plugin"]), "id": f"mat-{material_id}"})
    if params["plugin"] == "itu-radio-material":
        ET.SubElement(bsdf, "string", {"name": "type", "value": str(params["itu_type"])})
        keys = ("thickness", "scattering_coefficient", "xpd_coefficient")
    else:
        keys = (
            "relative_permittivity",
            "conductivity",
            "thickness",
            "scattering_coefficient",
            "xpd_coefficient",
        )
    for key in keys:
        ET.SubElement(bsdf, "float", {"name": key, "value": str(params[key])})
    if "color" in params:
        ET.SubElement(bsdf, "rgb", {"name": "color", "value": str(params["color"])})
    return bsdf
