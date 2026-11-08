#!/usr/bin/env python3
"""Isothermal ventilation diagnostic with kinematic pressure, separate from thermal case."""

import json
import re
import shutil
from contextlib import nullcontext

from generate import header
from model_parameters import fan_m3_h, fan_m3_s, mesh_revision
from parallel import preflight, tool
from run_case import ROOT, case_lock, stage


def main(already_locked=False, iterations=30):
    case = ROOT / "validation/isothermal"
    with nullcontext() if already_locked else case_lock():
        preflight(stage, ROOT)
        if case.exists():
            import time

            history = ROOT / "validation/history"
            history.mkdir(parents=True, exist_ok=True)
            shutil.move(str(case), str(history / f"isothermal-{time.time_ns()}"))
        case.mkdir(parents=True)
        for name in ["0", "system", "geometry"]:
            shutil.copytree(ROOT / name, case / name)
        for unused in ["T", "p_rgh", "alphat"]:
            (case / "0" / unused).unlink(missing_ok=True)
        params = json.loads((ROOT / "geometry/parameters.json").read_text())
        u_path = case / "0/U"
        u = u_path.read_text()
        u = re.sub(
            r"(volumetricFlowRate\s+constant\s+)[^;]+;",
            lambda m: m[1] + f"{fan_m3_s(params):.14g};",
            u,
        )
        u_path.write_text(u)
        print(f"Cold-flow target: {fan_m3_h(params):.3f} м³/ч; heating disabled.", flush=True)
        (case / "constant").mkdir()
        shutil.copy2(ROOT / "constant/turbulenceProperties", case / "constant/turbulenceProperties")
        shutil.copytree(
            ROOT / "constant/polyMesh",
            case / "constant/polyMesh",
            ignore=shutil.ignore_patterns("sets"),
        )
        (case / "constant/transportProperties").write_text(
            header("transportProperties")
            + "transportModel Newtonian;\nnu [0 2 -1 0 0 0 0] 1.503e-5;\n"
        )
        (case / "0/p").write_text(
            header("p", "volScalarField")
            + """dimensions [0 2 -2 0 0 0 0];
internalField uniform 0;
boundaryField
{
 ambient { type totalPressure; p0 uniform 0; value uniform 0; }
 exhaust { type zeroGradient; }
 "(floor|grillWalls|hoodWalls|heater|meatWalls|skewerWalls)" { type zeroGradient; }
}
"""
        )
        schemes = case / "system/fvSchemes"
        schemes.write_text(
            schemes.read_text().replace(
                "div(((rho*nuEff)*dev2(T(grad(U))))) Gauss linear;",
                "div((nuEff*dev2(T(grad(U))))) Gauss linear;",
            )
        )
        control = case / "system/controlDict"
        control.write_text(
            header("controlDict")
            + """application simpleFoam;
startFrom startTime; startTime 0; stopAt endTime; endTime 30; deltaT 1;
writeControl timeStep; writeInterval 10; purgeWrite 3; writeFormat ascii; writePrecision 10;
runTimeModifiable true;
functions
{
 ranges { type fieldMinMax; libs (fieldFunctionObjects); fields (U p); mode magnitude; writeControl timeStep; writeInterval 5; }
 flow { type surfaceFieldValue; libs (fieldFunctionObjects); regionType patch; name exhaust; operation areaNormalIntegrate; fields (U); writeFields false; writeControl timeStep; writeInterval 1; }
}
"""
        )
        control.write_text(control.read_text().replace("endTime 30;", f"endTime {iterations};"))
        solution = case / "system/fvSolution"
        text = (
            solution.read_text()
            .replace("p_rgh 0.3; rho 0.3;", "p 0.3;")
            .replace("momentumPredictor no;", "momentumPredictor yes;")
        )
        solution.write_text(text)
        (case / "geometry/mesh-revision.json").write_text(
            json.dumps(mesh_revision(ROOT), indent=2) + "\n"
        )
        tool(
            stage,
            "Isothermal ventilation",
            "simpleFoam",
            [],
            case,
            case / "logs/simpleFoam.log",
            kind="solve",
            selection="0",
        )


if __name__ == "__main__":
    main()
