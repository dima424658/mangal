#!/usr/bin/env python3
"""Fail closed on missing boundaries, mesh errors, or incorrect source settings."""

import argparse
import json
import math
import re
from pathlib import Path

from model_parameters import CFM_TO_M3_S, fan_cfm, fan_m3_s

ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise SystemExit("FAIL: " + message)


def latest_data(name):
    files = list((ROOT / "postProcessing" / name).glob("*/*.dat"))
    require(files, f"No data for {name}")
    f = max(files, key=lambda p: float(p.parent.name))
    rows = [s for s in f.read_text().splitlines() if s.strip() and not s.startswith("#")]
    require(rows, f"Empty output: {f}")
    return [float(x) for x in re.findall(r"[-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?", rows[-1])]


def main():
    global ROOT
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", type=Path, default=ROOT)
    parser.add_argument("--mesh", action="store_true")
    parser.add_argument("--result", action="store_true")
    args = parser.parse_args()
    ROOT = args.case.resolve()
    p = json.loads((ROOT / "geometry/parameters.json").read_text())
    require(fan_cfm(p) > 0, "Expected positive exhaust flow")
    target_q = fan_m3_s(p)
    require(p["physics"]["heat_to_air_W"] == 4000, "Expected 4000 W")
    require(p["environment"]["wind_m_s"] == 0, "Wind BC is implemented for still ambient air only")
    text = (ROOT / "0/U").read_text()
    match = re.search(r"volumetricFlowRate\s+constant\s+([\d.eE+-]+)", text)
    require(
        match and math.isclose(float(match[1]), target_q, rel_tol=1e-10), "Exhaust volumetric BC"
    )
    thermal = (ROOT / "0/T").read_text()
    fixed = bool(re.search(r"Q\s+constant\s+4000\s*;", thermal))
    ramp = bool(
        re.search(r"Q\s+table\s*\(\s*\(0 0\)\s*\(40 4000\)\s*\(2000 4000\)\s*\)\s*;", thermal)
    )
    require(fixed or ramp, "Expected heater power 4000 W, with optional documented startup ramp")
    expected = {"floor", "ambient", "exhaust", "grillWalls", "heater", "hoodWalls"}
    if p.get("skewers", {}).get("enabled"):
        expected.update(["meatWalls", "skewerWalls"])
    for f in ("U", "T", "p", "p_rgh", "k", "omega", "nut", "alphat"):
        text = (ROOT / "0" / f).read_text()
        require(
            all(re.search(r"\b" + b + r"\s*\{", text) for b in expected), f"{f}: patch coverage"
        )
    if args.mesh or args.result:
        require(
            not (ROOT / "geometry/MESH_REBUILD_REQUIRED").exists(),
            "Geometry changed; rebuild mesh with ./mangal mesh",
        )
        text = (ROOT / "constant/polyMesh/boundary").read_text()
        patches = {
            m[1]: int(m[2])
            for m in re.finditer(r"(\w+)\s*\{[^{}]*?nFaces\s+(\d+)\s*;[^{}]*?\}", text, re.S)
        }
        require(set(patches) == expected, f"Mesh patch names: {patches}")
        require(all(patches[b] > 0 for b in expected), f"Empty patch: {patches}")
        check = (ROOT / "logs/checkMesh.log").read_text()
        require(
            "Mesh OK." in check and "Failed " not in check,
            "checkMesh failed; inspect logs/checkMesh.log",
        )
        require(
            re.search(r"Number of regions:\s+1\b", check) is not None,
            "Fluid must be one connected region",
        )
        print("Mesh patches:", patches)
        extended = ROOT / "logs/checkMesh-extended.log"
        if extended.exists() and "Failed " in extended.read_text():
            print(
                "NOTE: extended geometric diagnostics reported concave polyhedral cells; see checkMesh-extended.log and VALIDATION.md."
            )
    if args.result:
        log = (ROOT / "logs/buoyantSimpleFoam.log").read_text()
        require(
            "FOAM FATAL" not in log and re.search(r"^End\s*$", log, re.M),
            "Solver did not finish normally",
        )
        q = latest_data("outletVolumeFlow")[1]
        files = list((ROOT / "postProcessing/outletVolumeFlow").glob("*/*.dat"))
        data = max(files, key=lambda f: float(f.parent.name)).read_text()
        area = float(re.search(r"# Area\s*:\s*([\d.eE+-]+)", data)[1])
        expected_area = math.pi * p["duct"]["second_diameter"] ** 2 / 4
        require(
            math.isclose(area, expected_area, rel_tol=0.02),
            f"Exhaust area {area} differs from round duct {expected_area}",
        )
        require("Did not find location" not in log, "A probe is outside the fluid mesh")
        probe_files = list((ROOT / "postProcessing/holeProbes").glob("*/T"))
        require(probe_files, "Missing probes in ventilation holes")
        probe = max(probe_files, key=lambda f: float(f.parent.name))
        rows = [r for r in probe.read_text().splitlines() if r.strip() and not r.startswith("#")]
        values = [float(v) for v in rows[-1].split()]
        require(
            len(values) == 9 and all(math.isfinite(v) and 0 < v < 10000 for v in values[1:]),
            "Invalid fluid samples in one or more ventilation holes",
        )
        heat = latest_data("heaterPower")[1]
        require(math.isclose(q, target_q, rel_tol=1e-5), f"Outlet Q={q} m3/s")
        require(math.isclose(abs(heat), 4000, rel_tol=1e-3), f"Heater integral={heat} W")
        print(f"Outlet: {q / CFM_TO_M3_S:.6f} CFM; area: {area:.8f} m2; wall heat: {heat:.6f} W")
        mass_out = latest_data("outletMassFlow")[1]
        mass_in = latest_data("ambientMassFlow")[1]
        print(f"Mass imbalance / outlet: {abs(mass_out + mass_in) / max(abs(mass_out), 1e-30):.4%}")
        print(
            "SIMPLE convergence:",
            "reported"
            if "SIMPLE solution converged" in log
            else "not reached; inspect residuals and balances",
        )
    print("Configuration checks passed.")


if __name__ == "__main__":
    main()
