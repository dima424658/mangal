#!/usr/bin/env python3
"""Measure each real ventilation passage on a cut through its wall thickness."""

import argparse
import json
import math
import re
from pathlib import Path

from generate import header
from model_parameters import M3_H_TO_M3_S
from parallel import configure, preflight, tool
from run_case import ROOT, case_lock, stage


def audit(case, cached=False):
    if not cached:
        preflight(stage, case)
    params = json.loads((case / "geometry/parameters.json").read_text())
    g = params["grill"]
    t = params["assumptions"]["wall_thickness_m"]
    r = g["hole_diameter"] / 2
    entries = []
    surfaces = []
    definitions = []
    for side, label in [(-1, "front"), (1, "rear")]:
        y = g.get("offset_y_m", 0) + side * (g["depth"] / 2 - t / 2)
        normal = -side
        for i, x0 in enumerate(g["hole_centers_x_assumed"]):
            x = x0 + g.get("offset_x_m", 0)
            z = g["hole_center_z_assumed"]
            name = f"{label}_{i + 1}"
            cut = f"""type cuttingPlane; planeType pointAndNormal;
pointAndNormalDict {{ point ({x} {y} {z}); normal (0 {normal} 0); }}
bounds ({x - r * 1.2} {y - 0.02} {z - r * 1.2}) ({x + r * 1.2} {y + 0.02} {z + r * 1.2}); interpolate true;"""
            entries.append(f"""holeFlux_{name}
{{ type surfaceFieldValue; libs (fieldFunctionObjects); regionType sampledSurface; name {name};
operation areaNormalIntegrate; fields (U); writeFields false; writeArea true; empty-surface warn;
sampledSurfaceDict {{ {cut} }} }}""")
            surfaces.append(f"{name} {{ {cut} }}")
            definitions.append(
                {
                    "hole": name,
                    "center_m": [x, y, z],
                    "positive_direction": "into grill",
                    "inward_normal": [0, normal, 0],
                }
            )
    entries.append(
        "holeCuts { type surfaces; libs (sampling); surfaceFormat vtk; interpolationScheme cellPoint; fields (U); surfaces { "
        + " ".join(surfaces)
        + " } }"
    )
    path = case / "system/holesAuditDict"
    path.write_text(header("holesAuditDict") + "functions\n{\n" + "\n".join(entries) + "\n}\n")
    if not cached:
        tool(
            stage,
            "Ventilation-hole flux audit",
            "postProcess",
            ["-dict", "system/holesAuditDict", "-fields", "(U)", "-latestTime"],
            case,
            case / "logs/holes-audit.log",
        )
    result = []
    for item in definitions:
        folder = case / "postProcessing" / ("holeFlux_" + item["hole"])
        files = list(folder.glob("*/*.dat"))
        if not files:
            result.append({**item, "status": "no sampled surface", "area_m2": 0})
            continue
        f = max(files, key=lambda p: float(p.parent.name))
        text = f.read_text()
        area = re.search(r"# Area\s*:\s*([\d.eE+-]+)", text)
        rows = [s for s in text.splitlines() if s.strip() and not s.startswith("#")]
        values = (
            [float(v) for v in re.findall(r"[-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?", rows[-1])]
            if rows
            else []
        )
        a = values[1] if len(values) >= 3 else (float(area[1]) if area else 0)
        result.append(
            {
                **item,
                "status": "sampled" if a > 0 else "closed or unresolved",
                "area_m2": a,
                "iteration": values[0] if values else None,
                "area_fraction_of_nominal": a / (math.pi * r * r),
                "flow_m3_h_positive_into_grill": values[-1] / M3_H_TO_M3_S
                if len(values) > 1
                else None,
            }
        )
    output = case / "results"
    output.mkdir(exist_ok=True)
    (output / "holes.json").write_text(json.dumps(result, indent=2) + "\n")
    lines = [
        "# Потоки через отверстия",
        "",
        "Плюс — внутрь чаши. Расходы в м³/ч.",
        "",
        "| Отверстие | Итерация | Площадь, мм² | Расход, м³/ч |",
        "|---|---:|---:|---:|",
    ]
    for row in result:
        flow = row.get("flow_m3_h_positive_into_grill")
        value = f"{flow:.4f}" if flow is not None else "нет данных"
        lines.append(
            f"| {row['hole']} | {row.get('iteration')} | {row['area_m2'] * 1e6:.2f} | {value} |"
        )
    (output / "HOLES.md").write_text("\n".join(lines) + "\n")
    for row in result:
        print(
            row["hole"],
            row["status"],
            "area",
            row["area_m2"],
            "m2; Q",
            row.get("flow_m3_h_positive_into_grill"),
            "м³/ч",
            flush=True,
        )
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", type=Path, default=ROOT / "runs/charcoal")
    parser.add_argument("--parallel", nargs="?", const="nproc", default="nproc")
    parser.add_argument("--cached", action="store_true")
    args = parser.parse_args()
    configure(args.parallel)
    with case_lock():
        audit(args.case.resolve(), cached=args.cached)


if __name__ == "__main__":
    main()
