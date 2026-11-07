#!/usr/bin/env python3
"""Build an isolated thermal/porous charcoal case on the existing mesh."""

import json
import math
import re
import shutil
from contextlib import ExitStack

from charcoal import bed_bounds, ergun
from generate import header
from model_parameters import fan_cfm, mesh_revision, mesh_signature
from parallel import preflight, tool
from run_case import ROOT, case_lock, current_mesh, stage

CASE = ROOT / "runs/charcoal"


def scalar_values(path, stack):
    stream = stack.enter_context(path.open())
    for line in stream:
        if line.strip().startswith("internalField"):
            if "nonuniform" not in line:
                raise ValueError(f"Expected nonuniform scalar data: {path}")
            break
    n = int(next(stream).strip())
    assert next(stream).strip() == "("
    return n, (float(next(stream).strip()) for _ in range(n))


def prepare():
    preflight(stage, ROOT)
    p = json.loads((ROOT / "geometry/parameters.json").read_text())
    if not current_mesh():
        raise RuntimeError("Build the current grill/hood mesh before preparing charcoal")
    cold = ROOT / "validation/isothermal"
    ready = False
    try:
        saved = json.loads((cold / "geometry/parameters.json").read_text())
        revision = json.loads((cold / "geometry/mesh-revision.json").read_text())
        log = (cold / "logs/simpleFoam.log").read_text()
        ready = (
            mesh_signature(saved) == mesh_signature(p)
            and math.isclose(fan_cfm(saved), fan_cfm(p), rel_tol=1e-10)
            and revision == mesh_revision(ROOT)
            and log.rstrip().endswith("End")
            and "FOAM FATAL" not in log
        )
    except (FileNotFoundError, ValueError):
        pass
    if not ready:
        from isothermal_start import main as cold_start

        print("Preparing a matching cold initial field before charcoal heating.", flush=True)
        cold_start(already_locked=True, iterations=30)
    times = [d for d in cold.iterdir() if d.is_dir() and re.fullmatch(r"[1-9]\d*", d.name)]
    latest = max(times, key=lambda d: float(d.name))
    if CASE.exists():
        history = ROOT / "runs/history"
        history.mkdir(parents=True, exist_ok=True)
        import time

        shutil.move(str(CASE), str(history / f"charcoal-{time.time_ns()}"))
    CASE.mkdir(parents=True)
    for name in ["0", "system", "geometry"]:
        shutil.copytree(ROOT / name, CASE / name, ignore=shutil.ignore_patterns("__pycache__"))
    (CASE / "geometry/MESH_REBUILD_REQUIRED").unlink(missing_ok=True)
    (CASE / "constant/polyMesh").mkdir(parents=True)
    for source in (ROOT / "constant").iterdir():
        if source.is_file() and source.name != "fvOptions":
            shutil.copy2(source, CASE / "constant" / source.name)
    for source in (ROOT / "constant/polyMesh").iterdir():
        if source.name in ("sets",) or source.is_dir():
            continue
        dest = CASE / "constant/polyMesh" / source.name
        if source.name == "cellZones":
            shutil.copy2(source, dest)
        else:
            shutil.copy2(source, dest)
    for name in ["U", "k", "omega", "nut"]:
        shutil.copy2(latest / name, CASE / "0" / name)
    # Isothermal p is kinematic. Convert it to absolute thermodynamic pressure
    # and p_rgh, retaining an isothermal hydrostatic atmosphere.
    if not (cold / "0/Cz").exists():
        tool(
            stage,
            "Cell centres",
            "postProcess",
            ["-func", "writeCellCentres", "-time", "0"],
            cold,
            CASE / "logs/centres.log",
            kind="field-write",
            selection="0",
        )
    temp = p["environment"]["air_temperature_K"]
    gas_R = 8314.46261815324 / 28.96
    p_atm = 101325.0
    rho_atm = p_atm / (gas_R * temp)
    templates = {name: (CASE / "0" / name).read_text() for name in ["p", "p_rgh"]}
    with ExitStack() as stack:
        n, pressures = scalar_values(latest / "p", stack)
        m, zvalues = scalar_values(cold / "0/Cz", stack)
        assert n == m
        outputs = {name: stack.enter_context((CASE / "0" / name).open("w")) for name in templates}
        for name, f in outputs.items():
            f.write(
                templates[name].split("internalField")[0]
                + f"internalField nonuniform List<scalar>\n{n}\n(\n"
            )
        for pressure, z in zip(pressures, zvalues):
            absolute = p_atm * math.exp(-9.81 * z / (gas_R * temp)) + rho_atm * pressure
            assert 0.8 * p_atm < absolute < 1.2 * p_atm
            reduced = absolute + absolute / (gas_R * temp) * 9.81 * z
            outputs["p"].write(f"{absolute:.12g}\n")
            outputs["p_rgh"].write(f"{reduced:.12g}\n")
        for name, f in outputs.items():
            f.write(");\n" + templates[name][templates[name].index("boundaryField") :])
    tfile = CASE / "0/T"
    text = tfile.read_text()
    text = re.sub(r"heater\s*\{[^{}]*\}", "heater { type zeroGradient; }", text)
    tfile.write_text(text)
    # Remove unused old boundary-heating function objects; record all source
    # heat via known absolute fvOption and the inlet/outlet enthalpy budget.
    control = CASE / "system/controlDict"
    text = control.read_text()
    for name in ["heatFlux", "heaterPower"]:
        text = re.sub(r"\b" + name + r"\s*\{[^{}]*\}", "", text)
    text = re.sub(r"\bendTime\s+[^;]+;", "endTime 80;", text)
    text = re.sub(r"^writeInterval\s+[^;]+;", "writeInterval 20;", text, flags=re.M)
    text = text.replace("startFrom startTime;", "startFrom startTime;")
    control.write_text(text.replace("writeInterval 25;", "writeInterval 20;"))
    solution = CASE / "system/fvSolution"
    s = solution.read_text().replace('"(U|h|k|omega)"', '"(U|k|omega)"')
    s = s.replace(
        "solvers\n{",
        "solvers\n{\n h { solver PBiCGStab; preconditioner DILU; tolerance 1e-9; relTol 0.001; }",
    )
    s = s.replace("U 0.3; h 0.5;", "U 0.3; h table ((0 0.7) (82 0.7) (83 1) (2000 1));")
    solution.write_text(s)
    low, high = bed_bounds(p)
    lo = " ".join(map(str, low))
    hi = " ".join(map(str, high))
    heat_low = [low[0], low[1], high[2]]
    heat_high = [high[0], high[1], high[2] + p["charcoal"]["heat_release_layer_m"]]
    hlo = " ".join(map(str, heat_low))
    hhi = " ".join(map(str, heat_high))
    (CASE / "system/charcoalTopoSetDict").write_text(
        header("charcoalTopoSetDict")
        + f"""actions
(
 {{ name charcoalCells; type cellSet; action new; source boxToCell; sourceInfo {{ box ({lo}) ({hi}); }} }}
 {{ name charcoalBed; type cellZoneSet; action new; source setToCellZone; sourceInfo {{ set charcoalCells; }} }}
 {{ name charcoalHeatCells; type cellSet; action new; source boxToCell; sourceInfo {{ box ({hlo}) ({hhi}); }} }}
 {{ name charcoalHeatLayer; type cellZoneSet; action new; source setToCellZone; sourceInfo {{ set charcoalHeatCells; }} }}
);
"""
    )
    d, f = ergun(p)
    power = p["charcoal"]["heat_to_air_W"]
    ramp = p["charcoal"]["startup_ramp_iterations"]
    (CASE / "constant/fvOptions").write_text(
        header("fvOptions")
        + f"""charcoalHeat
{{
 type scalarSemiImplicitSource;
 selectionMode cellZone; cellZone charcoalHeatLayer;
 volumeMode absolute;
 sources
 {{
  h {{ explicit table ((0 0) ({ramp} {power}) (2000 {power})); implicit none; }}
 }}
}}
charcoalResistance
{{
 type explicitPorositySource; active true;
 explicitPorositySourceCoeffs
 {{
  type DarcyForchheimer; selectionMode cellZone; cellZone charcoalBed;
  DarcyForchheimerCoeffs
  {{
   d d [0 -2 0 0 0 0 0] ({d} {d} {d});
   f f [0 -1 0 0 0 0 0] ({f} {f} {f});
   coordinateSystem {{ origin (0 0 0); e1 (1 0 0); e2 (0 1 0); }}
  }}
 }}
}}
"""
    )
    report = {
        "fan_cfm": fan_cfm(p),
        "cold_seed": str(latest.relative_to(ROOT)),
        "bed_bounds_m": [low, high],
        "heat_release_bounds_m": [heat_low, heat_high],
        "geometric_bed_volume_m3": math.prod(high[i] - low[i] for i in range(3)),
        "heat_W": power,
        "heat_ramp_iterations": ramp,
        "darcy_m_minus2": d,
        "forchheimer_m_minus1": f,
        "assumptions": p["charcoal"],
    }
    (CASE / "geometry/charcoal.json").write_text(json.dumps(report, indent=2) + "\n")
    tool(
        stage,
        "Select charcoal cells",
        "topoSet",
        ["-dict", "system/charcoalTopoSetDict"],
        CASE,
        CASE / "logs/topoSet-charcoal.log",
        kind="mesh",
        selection="0",
    )
    print(json.dumps(report, indent=2), flush=True)


def run(iterations=120, resume=False):
    preflight(stage, ROOT)
    if resume:
        p = json.loads((ROOT / "geometry/parameters.json").read_text())
        saved = json.loads((CASE / "geometry/parameters.json").read_text())
        if p != saved:
            raise RuntimeError(
                "Parameters changed; prepare a new charcoal case instead of resuming"
            )
        if not list(CASE.glob("[1-9]*")):
            raise RuntimeError("No charcoal checkpoint to resume")
    else:
        prepare()
    path = CASE / "system/controlDict"
    text = path.read_text()
    text = re.sub(r"\bendTime\s+[^;]+;", f"endTime {iterations};", text)
    text = re.sub(
        r"\bstartFrom\s+[^;]+;", "startFrom latestTime;" if resume else "startFrom startTime;", text
    )
    path.write_text(text)
    tool(
        stage,
        "Porous charcoal thermal run",
        "buoyantSimpleFoam",
        [],
        CASE,
        CASE / "logs/buoyantSimpleFoam.log",
        kind="solve",
        selection="latest" if resume else "0",
    )
    from charcoal_report import report

    report()
    (CASE / "charcoal.foam").touch()


def main():
    with case_lock():
        prepare()


if __name__ == "__main__":
    main()
