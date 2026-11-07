#!/usr/bin/env python3
"""Generate Blender OBJ directly from parameters; no STL read or write."""

import copy
import hashlib
import json
import math
from pathlib import Path

from charcoal import bed_bounds, lumps
from generate import grill, hood
from model_parameters import fan_cfm, mesh_signature

ROOT = Path(__file__).resolve().parents[1]


def main():
    params = ROOT / "geometry/parameters.json"
    p = json.loads(params.read_text())
    out = ROOT / "preview"
    out.mkdir(exist_ok=True)
    visual = copy.deepcopy(p)
    # The extra 40 mm in CFD geometry crosses the domain ceiling; omit it in the physical preview.
    visual["assumptions"]["duct_mesh_extension_m"] = 0.0
    hood_shape, outlet_z = hood(visual)
    shapes = [(grill(p), "Grill"), (hood_shape, "Hood_and_ducts")]
    from skewers import shapes as skewer_shapes

    shapes += [(s, f"Skewer_{i + 1:02d}") for i, s in enumerate(skewer_shapes(p))]
    # Validate and orient both solids before replacing the preview.
    checked = [(shape.validated_regions(), name) for shape, name in shapes]
    (out / "mangal.mtl").write_text(
        "newmtl meat\nKd 0.55 0.16 0.12\nKs 0.12 0.12 0.12\nNs 12\n"
        "newmtl steel\nKd 0.42 0.47 0.52\nKs 0.6 0.6 0.6\nNs 80\n"
        "newmtl heater\nKd 0.85 0.18 0.04\nKs 0.1 0.1 0.1\nNs 10\n"
        "newmtl fan_marker\nKd 0.1 0.55 0.9\nd 0.35\n"
        "newmtl charcoal\nKd 0.055 0.05 0.045\nKs 0.08 0.08 0.08\nNs 8\n"
    )
    count = 0
    report = {
        "parameters_sha256": hashlib.sha256(params.read_bytes()).hexdigest(),
        "units": "m",
        "exhaust_cfm": fan_cfm(p),
        "objects": {},
    }
    with (out / "mangal.obj").open("w") as obj:
        obj.write("# Units: metres. Geometry only, not CFD solution.\nmtllib mangal.mtl\n")
        for (regions, stats), name in checked:
            obj.write(f"o {name}\n")
            points = [pt for tris in regions.values() for tri in tris for pt in tri]
            stats["bounds_m"] = [
                [min(pt[i] for pt in points), max(pt[i] for pt in points)] for i in range(3)
            ]
            report["objects"][name] = stats
            for region, triangles in regions.items():
                obj.write(
                    "usemtl "
                    + (
                        "heater"
                        if region == "heater"
                        else "meat"
                        if region == "meatWalls"
                        else "steel"
                    )
                    + "\n"
                )
                for triangle in triangles:
                    for pt in triangle:
                        obj.write("v " + " ".join(f"{v:.12g}" for v in pt) + "\n")
                    obj.write(f"f {count + 1} {count + 2} {count + 3}\n")
                    count += 3
        if p.get("charcoal", {}).get("enabled"):
            pieces = lumps(p)
            obj.write("o Charcoal_bed\nusemtl charcoal\n")
            for vertices, faces in pieces:
                start = count
                for point in vertices:
                    obj.write("v " + " ".join(f"{v:.12g}" for v in point) + "\n")
                for face in faces:
                    obj.write("f " + " ".join(str(start + index + 1) for index in face) + "\n")
                count += len(vertices)
            report["charcoal"] = {
                "lumps": len(pieces),
                "bounds_m": bed_bounds(p),
                "porous_CFD_model": p["charcoal"],
                "individual_lumps_are_visual_only": True,
            }
        if p.get("fan", {}).get("placement") == "duct_outlet":
            radius = p["duct"]["second_diameter"] / 2
            obj.write("o Fan_outlet_marker\nusemtl fan_marker\n")
            obj.write(f"v 0 0 {outlet_z:.12g}\n")
            center = count + 1
            for i in range(64):
                angle = 2 * math.pi * i / 64
                obj.write(
                    f"v {radius * math.cos(angle):.12g} {radius * math.sin(angle):.12g} {outlet_z:.12g}\n"
                )
            for i in range(64):
                obj.write(f"f {center} {center + 1 + i} {center + 1 + (i + 1) % 64}\n")
            count += 65
            report["fan"] = {
                "position_m": [0, 0, outlet_z],
                "diameter_m": 2 * radius,
                "flow_cfm": fan_cfm(p),
                "representation": "symbolic open disk, not a solid CFD wall",
            }
    back_y = report["objects"]["Grill"]["bounds_m"][1][1]
    report["rear_clearance_m"] = p["hood"]["inlet_depth"] / 2 - back_y
    (out / "geometry.json").write_text(json.dumps(report, indent=2) + "\n")
    stamp = ROOT / "constant/polyMesh/model-parameters.json"
    if not stamp.exists() or mesh_signature(json.loads(stamp.read_text())) != mesh_signature(p):
        (ROOT / "geometry/MESH_REBUILD_REQUIRED").write_text(
            "OBJ geometry updated. CFD STL and mesh are older revisions; regenerate before solving.\n"
        )
    print(
        f"{out / 'mangal.obj'}: {sum(stats['triangles'] for stats in report['objects'].values())} solid triangles, rear clearance {report['rear_clearance_m']:.3f} m"
    )


if __name__ == "__main__":
    main()
