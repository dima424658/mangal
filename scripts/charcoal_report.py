#!/usr/bin/env python3
"""Validate/report the porous charcoal run without confusing iteration limits with convergence."""

import json
import math
import re

from model_parameters import M3_H_TO_M3_S, fan_m3_h
from run_case import ROOT

CASE = ROOT / "runs/charcoal"
NUM = r"[-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?"


def row(name, iteration):
    files = sorted(
        (CASE / "postProcessing" / name).glob("*/*.dat"),
        key=lambda p: float(p.parent.name),
        reverse=True,
    )
    for file in files:
        matches = []
        for line in file.read_text().splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            values = [float(v) for v in re.findall(NUM, line)]
            if values and math.isclose(values[0], iteration, rel_tol=0, abs_tol=1e-9):
                matches.append(values)
        if matches:
            return matches[-1]
    raise RuntimeError(f"Missing {name} monitoring data at saved iteration {iteration:g}")


def report(require_finished=True):
    params = json.loads((CASE / "geometry/parameters.json").read_text())
    model = json.loads((CASE / "geometry/charcoal.json").read_text())
    log = (CASE / "logs/buoyantSimpleFoam.log").read_text(errors="replace")
    if "FOAM FATAL" in log:
        raise RuntimeError("Charcoal solver failed; inspect run log")
    finished = bool(re.search(r"^End\s*$", log, re.M))
    if require_finished and not finished:
        raise RuntimeError("Charcoal solver has not finished")
    times = re.findall(r"^Time = (" + NUM + r")$", log, re.M)
    from parallel import time_name

    iteration = float(time_name(CASE, "latest"))
    solver_iteration = float(times[-1]) if times else iteration
    # Monitoring may extend beyond the last written fields after an intentional stop.
    snapshot_log = ""
    for candidate in [
        CASE / "logs/buoyantSimpleFoam.log",
        *sorted((CASE / "logs/history").glob("*-buoyantSimpleFoam.log"), reverse=True),
    ]:
        text = candidate.read_text(errors="replace")
        blocks = re.findall(r"^Time = (" + NUM + r")\n(.*?)(?=^Time = |\Z)", text, re.M | re.S)
        found = [chunk for t, chunk in blocks if float(t) == iteration]
        if found:
            snapshot_log = found[-1]
            break
    out_mass = row("outletMassFlow", iteration)[1]
    ambient_mass = row("ambientMassFlow", iteration)[1]
    flow = row("outletVolumeFlow", iteration)[1] / M3_H_TO_M3_S
    if not math.isclose(flow, fan_m3_h(params), rel_tol=1e-5):
        raise RuntimeError(f"Incorrect outlet м³/ч: {flow}")
    ambient_T = params["environment"]["air_temperature_K"]
    q_out = 1005 * (row("outletThermalFlux", iteration)[1] - ambient_T * out_mass)
    q_ambient = 1005 * (row("ambientThermalFlux", iteration)[1] - ambient_T * ambient_mass)
    source = model["heat_W"] * min(1, iteration / model["heat_ramp_iterations"])
    state = row("outletState", iteration)
    result = {
        "model": model,
        "iteration": iteration,
        "solver_last_iteration": solver_iteration,
        "solver_finished": finished,
        "simple_converged": "SIMPLE solution converged" in log,
        "fan_m3_h": flow,
        "source_power_W": source,
        "source_full_power_reached": iteration >= model["heat_ramp_iterations"],
        "mass_imbalance_fraction": abs(out_mass + ambient_mass) / max(abs(out_mass), 1e-30),
        "outlet_sensible_heat_W": q_out,
        "ambient_sensible_heat_W": q_ambient,
        "sensible_heat_balance_error_fraction": abs(q_out + q_ambient - source)
        / max(source, 1e-30),
        "outlet_mean_T_K": state[1],
        "outlet_mean_T_C": state[1] - 273.15,
        "outlet_mean_p_Pa": state[2],
    }
    for key, pattern in [
        ("min_T_K", r"min\(T\) = (" + NUM + ")"),
        ("max_T_K", r"max\(T\) = (" + NUM + ")"),
        ("max_U_m_s", r"max\(mag\(U\)\) = (" + NUM + ")"),
    ]:
        values = re.findall(pattern, snapshot_log)
        if values:
            result[key] = float(values[-1])
    if result.get("min_T_K", 293) <= 0 or result.get("max_U_m_s", 0) > 100:
        raise RuntimeError("Nonphysical temperature or velocity in the charcoal run")
    selected = re.findall(r"selected (\d+) cell\(s\) with volume (" + NUM + ")", log)
    result["selected_zones"] = [{"cells": int(n), "volume_m3": float(v)} for n, v in selected]
    result["engineering_result_ready"] = bool(
        result["simple_converged"]
        and result["source_full_power_reached"]
        and result["mass_imbalance_fraction"] < 0.01
        and result["sensible_heat_balance_error_fraction"] < 0.05
    )
    path = CASE / "results"
    path.mkdir(exist_ok=True)
    (path / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
    lines = [
        "# Угли: пористый слой и тепловой поток",
        "",
        "Сошедшийся предварительный RANS-расчёт."
        if result["engineering_result_ready"]
        else "Предварительный несошедшийся проход. Для инженерных выводов недостаточен.",
        "",
        f"Итерация: {iteration:g}; расход: {flow:.3f} м³/ч; заданный источник: {source:.1f} Вт.",
        f"Температура на выходе (средняя по площади): {result['outlet_mean_T_C']:.2f} °C.",
        f"Массовый дисбаланс: {result['mass_imbalance_fraction']:.4%}.",
        f"Вынос явного тепла вытяжкой: {q_out:.1f} Вт; открытыми границами: {q_ambient:.1f} Вт.",
        f"Невязка баланса явного тепла: {result['sensible_heat_balance_error_fraction']:.2%}.",
        "",
        "Тепловой поток на выходе не является измерением эффективности улавливания дыма.",
        "Химия горения, радиация, выгорание и тепловая инерция углей не моделируются.",
        "Толщина слоя 50 мм, условный размер куска 30 мм, доля пустот 45% — допущения.",
    ]
    (path / "REPORT.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines[:9]), flush=True)
    return result


if __name__ == "__main__":
    report()
