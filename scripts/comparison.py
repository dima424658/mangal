"""Isolated, restartable 2x2 comparison of hood clearance and exhaust flow."""

import argparse
import csv
import json
import math
import re
import shutil
from pathlib import Path

from model_parameters import canonical_parameters, mesh_signature, outlet_height
from run_case import ROOT, case_lock, stage

EXPERIMENT = ROOT / "runs/comparisons/hood-gap-fan"
VARIANTS = [
    ("50cm-350m3h", 0.50, 350),
    ("50cm-600m3h", 0.50, 600),
    ("35cm-350m3h", 0.35, 350),
    ("35cm-600m3h", 0.35, 600),
]


def parameters(base, gap, flow):
    p = canonical_parameters(base)
    shift = p["grill"]["height"] + gap - p["hood"]["bottom_z"]
    p["hood"]["bottom_z"] += shift
    p["hood"]["top_z"] += shift
    p["environment"]["domain_bounds_m"][5] = outlet_height(p)
    p["physics"]["exhaust_m3_h"] = float(flow)
    p["fan"]["flow_m3_h"] = float(flow)
    return p


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def prepare(name, p, base, donor=None):
    target = EXPERIMENT / name
    marker = target / "comparison.json"
    if marker.exists():
        if json.loads(marker.read_text())["parameters"] != p:
            raise RuntimeError(f"Existing variant has different parameters: {target}")
        return target
    if target.exists():
        raise RuntimeError(f"Incomplete variant directory: {target}; inspect before retrying")
    target.mkdir(parents=True)
    for folder in ["scripts", "system", "geometry", "0"]:
        shutil.copytree(
            ROOT / folder,
            target / folder,
            ignore=shutil.ignore_patterns("__pycache__", "MESH_REBUILD_REQUIRED"),
        )
    for filename in ["mangal", "pyproject.toml", "requirements-dev.txt"]:
        shutil.copy2(ROOT / filename, target / filename)
    (target / "constant").mkdir()
    for source in (ROOT / "constant").iterdir():
        if source.is_file():
            shutil.copy2(source, target / "constant" / source.name)
    save_json(target / "geometry/parameters.json", p)
    # Reuse only an independently owned mesh from exactly the same geometry.
    mesh_source = (donor or ROOT) / "constant/polyMesh"
    if (mesh_source / "model-parameters.json").exists() and mesh_signature(
        json.loads((mesh_source / "model-parameters.json").read_text())
    ) == mesh_signature(p):
        shutil.copytree(
            mesh_source, target / "constant/polyMesh", ignore=shutil.ignore_patterns("sets")
        )
    if (target / "constant/polyMesh/boundary").exists():
        (target / "logs").mkdir(exist_ok=True)
        for filename in ["checkMesh.log", "checkMesh-extended.log"]:
            shutil.copy2((donor or ROOT) / "logs" / filename, target / "logs" / filename)
    u = target / "0/U"
    text = u.read_text()
    text = re.sub(
        r"(volumetricFlowRate\s+constant\s+)[^;]+;",
        lambda m: m[1] + f"{p['physics']['exhaust_m3_h'] / 3600:.14g};",
        text,
    )
    u.write_text(text)
    if base:
        shutil.copytree(
            ROOT / "runs/charcoal",
            target / "runs/charcoal",
            ignore=lambda directory, names: [
                name
                for name in names
                if re.fullmatch(r"processor\d+|processors\d+|\.decomposition.json", name)
                or (
                    Path(directory) == ROOT / "runs/charcoal"
                    and name.replace(".", "", 1).isdigit()
                    and name not in ["0", "200"]
                )
            ],
        )
        # Snapshot metadata is migrated; the original case is untouched.
        save_json(target / "runs/charcoal/geometry/parameters.json", p)
        model = target / "runs/charcoal/geometry/charcoal.json"
        value = json.loads(model.read_text())
        value.pop("fan_cfm", None)
        value["fan_m3_h"] = 350.0
        save_json(model, value)
    save_json(
        marker,
        {
            "parameters": p,
            "gap_m": p["hood"]["bottom_z"] - p["grill"]["height"],
            "flow_m3_h": p["physics"]["exhaust_m3_h"],
        },
    )
    return target


def monitoring_window(case):
    from charcoal_report import NUM

    result = {}
    for name in ["outletState", "outletVolumeFlow", "outletThermalFlux"]:
        values = {}
        for file in sorted(
            (case / "postProcessing" / name).glob("*/*.dat"), key=lambda f: float(f.parent.name)
        ):
            for line in file.read_text().splitlines():
                if not line.strip() or line.lstrip().startswith("#"):
                    continue
                row = [float(v) for v in re.findall(NUM, line)]
                if row and 150 <= row[0] <= 200:
                    values[row[0]] = row[1]
        if values:
            ordered = [values[t] for t in sorted(values)]
            result[name] = {
                "first": ordered[0],
                "last": ordered[-1],
                "range": max(ordered) - min(ordered),
            }
    return result


def residual_window(case):
    from charcoal_report import NUM

    log = (case / "logs/buoyantSimpleFoam.log").read_text()
    result = {}
    blocks = re.findall(r"^Time = (" + NUM + r")\n(.*?)(?=^Time = |\Z)", log, re.M | re.S)
    for field in ["h", "p_rgh", "Ux", "Uy", "Uz", "k", "omega"]:
        values = []
        for t, chunk in blocks:
            if not 150 <= float(t) <= 200:
                continue
            match = re.search(
                r"Solving for " + field + r", Initial residual = (" + NUM + r")", chunk
            )
            if match:
                values.append(float(match[1]))
        if values:
            result[field] = {
                "first": values[0],
                "last": values[-1],
                "min": min(values),
                "max": max(values),
            }
    return result


def summarize():
    rows = []
    for name, gap, flow in VARIANTS:
        target = EXPERIMENT / name
        case = target / "runs/charcoal"
        if not (target / "COMPLETED.json").exists():
            continue
        result = json.loads((case / "results/summary.json").read_text())
        holes = json.loads((case / "results/holes.json").read_text())
        check = (target / "logs/checkMesh.log").read_text()
        extended = (target / "logs/checkMesh-extended.log").read_text()
        cells = re.search(r"cells:\s+(\d+)", check)
        row = {
            "variant": name,
            "gap_cm": round(gap * 100),
            "flow_m3_h": flow,
            "iteration": result["iteration"],
            "cells": int(cells[1]),
            "outlet_T_C": result["outlet_mean_T_C"],
            "mass_imbalance_pct": result["mass_imbalance_fraction"] * 100,
            "convective_balance_error_pct": result["sensible_heat_balance_error_fraction"] * 100,
            "outlet_heat_W": result["outlet_sensible_heat_W"],
            "ambient_heat_W": result["ambient_sensible_heat_W"],
            "hole_intake_m3_h": sum(h["flow_m3_h_positive_into_grill"] for h in holes),
            "simple_converged": result["simple_converged"],
        }
        rows.append(row)
        from comparison_plot import section_image

        section_image(target, f"{gap * 100:g} см / {flow} м³/ч")
        save_json(
            target / "comparison-diagnostics.json",
            {
                "summary": row,
                "monitoring_last_50": monitoring_window(case),
                "initial_residuals_last_50": residual_window(case),
                "extended_geometry_warning": "Failed " in extended,
                "holes": holes,
            },
        )
    if not rows:
        return
    with (EXPERIMENT / "comparison.csv").open("w") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    save_json(EXPERIMENT / "comparison.json", rows)
    lines = [
        "# ClearDraft: зазор зонта и расход",
        "",
        "Предварительное сравнение при 4 кВт, без ветра. Одинаковые 200 итераций не гарантируют сходимость.",
        "Доля захвата дыма не рассчитывается: пассивная примесь отсутствует. Баланс тепла — только конвективный.",
        "",
        "| Вариант | Зазор, см | Расход, м³/ч | Выход, °C | Дисбаланс массы, % | Невязка тепла, % | Приток отверстий, м³/ч |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| [{row['variant']}]({row['variant']}/visualization/charcoal/charcoal-flow.html) | {row['gap_cm']} | {row['flow_m3_h']} | {row['outlet_T_C']:.2f} | {row['mass_imbalance_pct']:.4f} | {row['convective_balance_error_pct']:.2f} | {row['hole_intake_m3_h']:.3f} |"
        )
    lines += ["", "## Сечения в одинаковой шкале", ""]
    for row in rows:
        lines.append(f"![{row['variant']}]({row['variant']}/front-temperature.svg)")
    lines += [
        "",
        "HTML-визуализации используют одинаковые камеры и шкалы: температура 20–150 °C, скорость 0–0,6 м/с. Сравнивайте одноимённые сечения и группы линий.",
        "Численные невязки и диапазоны мониторинга за итерации 150–200 находятся в comparison-diagnostics.json каждого варианта.",
    ]
    (EXPERIMENT / "REPORT.md").write_text("\n".join(lines) + "\n")
    from comparison_viewer import build

    build(EXPERIMENT, VARIANTS)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--parallel", default="nproc")
    args = parser.parse_args()
    with case_lock():
        p = json.loads((ROOT / "geometry/parameters.json").read_text())
        if not math.isclose(p["hood"]["bottom_z"] - p["grill"]["height"], 0.5):
            raise RuntimeError("Comparison expects the original 50 cm baseline")
        if not (ROOT / "runs/charcoal/200/U").exists():
            raise RuntimeError("Baseline fields at iteration 200 are required")
        # Copy the baseline before any new calculation; no root fields or outputs are modified.
        baseline = prepare(VARIANTS[0][0], parameters(p, 0.5, 350), True)
        for name, gap, flow in VARIANTS[1:] + VARIANTS[:1]:
            target = (
                baseline
                if name == VARIANTS[0][0]
                else prepare(
                    name,
                    parameters(p, gap, flow),
                    False,
                    EXPERIMENT / "35cm-350m3h" if gap == 0.35 and flow == 600 else None,
                )
            )
            if (target / "COMPLETED.json").exists():
                continue
            (target / "logs").mkdir(exist_ok=True)
            case = target / "runs/charcoal"
            complete = (case / "200/U").exists()
            if not complete:
                from parallel import time_name

                saved_times = [float(time_name(case, "latest"))] if case.exists() else [0]
                if case.exists():
                    saved_times += [
                        float(time_name(folder, "latest"))
                        for folder in case.glob("processor[0-9]*")
                        if folder.is_dir()
                    ]
                continuing = max(saved_times) > 0
                stage(
                    f"{name}: full CFD and exports",
                    [
                        "./mangal",
                        "all",
                        "--iterations",
                        "200",
                        "--coarse",
                        "--parallel",
                        args.parallel,
                    ]
                    + (["--resume"] if continuing else []),
                    target / "logs/comparison-run.log",
                    cwd=target,
                )
            else:
                for action in ["preview", "audit", "visualize"]:
                    command = ["./mangal", action] + (
                        ["--parallel", args.parallel] if action != "preview" else []
                    )
                    stage(
                        f"{name}: {action}",
                        command,
                        target / "logs" / f"comparison-{action}.log",
                        cwd=target,
                    )
            save_json(
                target / "COMPLETED.json", {"iteration": 200, "gap_m": gap, "flow_m3_h": flow}
            )
            summarize()
        summarize()
        print(f"Comparison ready: {EXPERIMENT}/REPORT.md", flush=True)


if __name__ == "__main__":
    main()
