#!/usr/bin/env python3
"""VTK and browser views of the computed gas temperature around the charcoal."""

import copy
import json
import re
import shutil
import xml.etree.ElementTree as ET

from charcoal import lumps
from cold_obj import export_obj
from cold_viewer import add_skewers, read_vtp, solid, write_geometry_vtp
from generate import grill, hood
from model_parameters import fan_cfm
from run_case import ROOT


def coal_data(params):
    points = []
    polys = []
    for vertices, triangles in lumps(params):
        start = len(points) // 3
        for pt in vertices:
            points.extend(pt)
        for tri in triangles:
            polys.extend([3, *(i + start for i in tri)])
    return {"points": points, "polys": polys, "lines": [], "speed": []}


def build():
    case = ROOT / "runs/charcoal"
    out = ROOT / "visualization/charcoal"
    out.mkdir(parents=True, exist_ok=True)
    p = json.loads((case / "geometry/parameters.json").read_text())
    summary = json.loads((case / "results/summary.json").read_text())
    times = [d for d in case.iterdir() if d.is_dir() and re.fullmatch(r"[1-9]\d*", d.name)]
    latest = max(times, key=lambda d: float(d.name)).name
    if summary["iteration"] != float(latest):
        raise RuntimeError("Report and saved fields belong to different iterations")
    data = {}
    for name, key in [
        ("frontSection", "front"),
        ("sideSection", "side"),
        ("horizontalSection", "horizontal"),
    ]:
        src = case / f"postProcessing/thermalSections/{latest}/{name}.vtp"
        dest = out / "sections" / src.name
        dest.parent.mkdir(exist_ok=True)
        shutil.copy2(src, dest)
        data[key] = read_vtp(dest)
    for name, key in [
        ("intakeStreamlines", "intake"),
        ("grillStreamlines", "grillFlow"),
        ("holeStreamlines", "holes"),
    ]:
        src = case / f"postProcessing/sets/{name}/{latest}/track0.vtp"
        dest = out / "streamlines" / f"{name}.vtp"
        dest.parent.mkdir(exist_ok=True)
        shutil.copy2(src, dest)
        data[key] = read_vtp(dest)
    physical = copy.deepcopy(p)
    physical["assumptions"]["duct_mesh_extension_m"] = 0
    data["grill"] = add_skewers(solid(grill(physical)), physical)
    data["hood"] = solid(hood(physical)[0])
    data["coal"] = coal_data(physical)
    geometry = out / "geometry"
    geometry.mkdir(exist_ok=True)
    for key in ["grill", "hood", "coal"]:
        write_geometry_vtp(data[key], geometry / f"{key}.vtp")
    root = ET.Element(
        "VTKFile", type="vtkMultiBlockDataSet", version="1.0", byte_order="LittleEndian"
    )
    blocks = ET.SubElement(root, "vtkMultiBlockDataSet")
    files = [
        ("Grill", "geometry/grill.vtp"),
        ("Hood", "geometry/hood.vtp"),
        ("Charcoal", "geometry/coal.vtp"),
        ("Front", "sections/frontSection.vtp"),
        ("Side", "sections/sideSection.vtp"),
        ("Horizontal", "sections/horizontalSection.vtp"),
        ("Intake_streams", "streamlines/intakeStreamlines.vtp"),
        ("Above_charcoal", "streamlines/grillStreamlines.vtp"),
        ("Hole_flows", "streamlines/holeStreamlines.vtp"),
    ]
    for i, (name, file) in enumerate(files):
        ET.SubElement(blocks, "DataSet", index=str(i), name=name, file=file)
    ET.ElementTree(root).write(out / "charcoal-view.vtm", encoding="utf-8", xml_declaration=True)
    from streamline_seeds import groups

    seed_counts = {name: len(points) for name, points in groups(p).items()}
    (out / "metadata.json").write_text(
        json.dumps(
            {
                "streamline_seed_counts": seed_counts,
                "iteration": int(latest),
                "phase": "gas temperature, not solid coal temperature",
                "summary": summary,
                "charcoal_model": p["charcoal"],
                "visual_lumps": len(lumps(p)),
            },
            indent=2,
        )
        + "\n"
    )
    for d in data.values():
        for key in ["points", "speed", "temperature_C"]:
            if key in d:
                d[key] = [float(f"{v:.7g}") for v in d[key]]
    text = (ROOT / "scripts/charcoal_viewer_template.html").read_text()
    replacements = {
        "__FLOW_CFM__": f"{fan_cfm(p):.3f}",
        "__ITERATION__": latest,
        "__POWER__": f"{summary['source_power_W']:.0f}",
        "__DATA_JSON__": json.dumps(data, separators=(",", ":"), allow_nan=False),
    }
    for key, value in replacements.items():
        text = text.replace(key, value)
    (out / "charcoal-flow.html").write_text(text)
    export_obj(
        out=out,
        params=physical,
        scalar="temperature_C",
        color_range=(20, 150),
        with_charcoal=True,
        basename="charcoal-flow",
    )
    print("Charcoal visualization:", out)


if __name__ == "__main__":
    build()
