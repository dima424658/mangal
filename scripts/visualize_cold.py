#!/usr/bin/env python3
"""Rebuild cold-flow visualizations with the native OpenFOAM VTK pipeline."""

import argparse
import json
import shutil

from cold_obj import export_obj
from cold_viewer import main as viewer
from model_parameters import fan_cfm, mesh_signature
from parallel import configure, preflight, tool
from run_case import ROOT, case_lock, stage


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--charcoal", action="store_true")
    parser.add_argument("--parallel", nargs="?", const="nproc", default="nproc")
    args = parser.parse_args()
    configure(args.parallel)
    if args.charcoal:
        from visualize_charcoal import main as heated

        return heated()
    case = ROOT / "validation/isothermal"
    if not (case / "logs/simpleFoam.log").exists():
        raise SystemExit("Run ./mangal run --cold before generating cold-flow views.")
    log = (case / "logs/simpleFoam.log").read_text()
    if not log.rstrip().endswith("End") or "FOAM FATAL" in log:
        raise SystemExit(
            "Cold solver did not finish normally; inspect validation/isothermal/logs/simpleFoam.log"
        )
    p = json.loads((ROOT / "geometry/parameters.json").read_text())
    saved = json.loads((case / "geometry/parameters.json").read_text())
    if mesh_signature(p) != mesh_signature(saved) or abs(fan_cfm(p) - fan_cfm(saved)) > 1e-8:
        raise SystemExit(
            "Cold result belongs to another geometry or CFM value. Run ./mangal run --cold again."
        )
    with case_lock():
        preflight(stage, case)
        shutil.copy2(ROOT / "system/coldVisualizationDict", case / "system/visualizeDict")
        tool(
            stage,
            "Cold sections and streamlines",
            "postProcess",
            ["-dict", "system/visualizeDict", "-fields", "(U p)", "-latestTime"],
            case,
            case / "logs/visualize.log",
        )
        tool(
            stage,
            "VTK export",
            "foamToVTK",
            [
                "-fields",
                "(U p)",
                "-latestTime",
                "-no-lagrangian",
                "-name",
                str(ROOT / "visualization/cold/VTK"),
            ],
            case,
            case / "logs/foamToVTK.log",
        )
        viewer()
        export_obj()


if __name__ == "__main__":
    main()
