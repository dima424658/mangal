#!/usr/bin/env python3
"""Export actual heated/porous CFD fields through native VTK and OBJ previews."""

import json
import shutil
import zipfile

from charcoal_report import CASE, report
from charcoal_viewer import build
from parallel import preflight, tool
from run_case import ROOT, case_lock, stage


def main():
    with case_lock():
        preflight(stage, CASE)
        current = json.loads((ROOT / "geometry/parameters.json").read_text())
        saved = json.loads((CASE / "geometry/parameters.json").read_text())
        if current != saved:
            raise RuntimeError("Charcoal result does not match current model parameters")
        result = report()
        if not result["source_full_power_reached"]:
            raise RuntimeError("The heat startup ramp has not reached full power")
        from streamline_seeds import update

        update(ROOT / "system/charcoalVisualizationDict", saved)
        shutil.copy2(ROOT / "system/charcoalVisualizationDict", CASE / "system/visualizeDict")
        tool(
            stage,
            "Heated sections and streamlines",
            "postProcess",
            ["-dict", "system/visualizeDict", "-fields", "(U T p)", "-latestTime"],
            CASE,
            CASE / "logs/visualize.log",
        )
        out = ROOT / "visualization/charcoal"
        tool(
            stage,
            "Heated VTK export",
            "foamToVTK",
            ["-fields", "(U T p)", "-latestTime", "-no-lagrangian", "-name", str(out / "VTK")],
            CASE,
            CASE / "logs/foamToVTK.log",
        )
        build()
        shutil.copy2(CASE / "results/REPORT.md", out / "REPORT.md")
        shutil.copy2(ROOT / "scripts/charcoal_visualization_readme.md", out / "README.md")
        with zipfile.ZipFile(out / "charcoal-preview.zip", "w", zipfile.ZIP_DEFLATED) as z:
            for name in [
                "README.md",
                "REPORT.md",
                "metadata.json",
                "charcoal-flow.html",
                "charcoal-flow.obj",
                "charcoal-flow.mtl",
                "charcoal-view.vtm",
            ]:
                z.write(out / name, "charcoal-preview/" + name)
            for folder in ["geometry", "sections", "streamlines"]:
                for path in (out / folder).glob("*.vtp"):
                    z.write(path, "charcoal-preview/" + str(path.relative_to(out)))


if __name__ == "__main__":
    main()
