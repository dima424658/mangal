#!/usr/bin/env python3
"""Summarize actual solver output, distinguishing a pilot from convergence."""

import csv
import json
import re
from pathlib import Path

from model_parameters import M3_H_TO_M3_S, fan_m3_h

ROOT = Path(__file__).resolve().parents[1]
NUM = r"[-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?"


def latest_row(name):
    files = list((ROOT / "postProcessing" / name).glob("*/*.dat"))
    if not files:
        return None
    path = max(files, key=lambda p: float(p.parent.name))
    rows = [
        line for line in path.read_text().splitlines() if line.strip() and not line.startswith("#")
    ]
    if not rows:
        return None
    return [float(x) for x in re.findall(NUM, rows[-1])]


def main():
    params = json.loads((ROOT / "geometry/parameters.json").read_text())
    log = (ROOT / "logs/buoyantSimpleFoam.log").read_text(errors="replace")
    report = {
        "model": params,
        "solver_completed": bool(re.search(r"^End\s*$", log, re.M)),
        "simple_converged": "SIMPLE solution converged" in log,
    }
    times = re.findall(r"^Time = (" + NUM + r")$", log, re.M)
    report["last_iteration"] = float(times[-1]) if times else None
    q = latest_row("outletVolumeFlow")
    heat = latest_row("heaterPower")
    mass_in = latest_row("ambientMassFlow")
    mass_out = latest_row("outletMassFlow")
    state = latest_row("outletState")
    if q:
        report["exhaust_m3_h"] = q[1] / M3_H_TO_M3_S
    if heat:
        report["heater_W"] = heat[1]
    if mass_in and mass_out:
        report["mass_imbalance_fraction"] = abs(mass_in[1] + mass_out[1]) / max(
            abs(mass_out[1]), 1e-30
        )
    if state and len(state) >= 6:
        report["outlet_mean_T_K"] = state[1]
        report["outlet_mean_p_Pa"] = state[2]
        report["outlet_mean_U_m_s"] = state[3:6]
    for name in ["min", "max"]:
        values = re.findall(r"\b" + name + r"\(T\)\s*=\s*(" + NUM + ")", log)
        if values:
            report[name + "_T_K"] = float(values[-1])
    for name, key, mass in [
        ("outletThermalFlux", "outlet_sensible_heat_W", mass_out),
        ("ambientThermalFlux", "ambient_sensible_heat_W", mass_in),
    ]:
        weighted = latest_row(name)
        if weighted and mass:
            report[key] = 1005 * (
                weighted[1] - params["environment"]["air_temperature_K"] * mass[1]
            )
    speeds = re.findall(r"max\(mag\(U\)\)\s*=\s*(" + NUM + ")", log)
    if speeds:
        report["max_U_m_s"] = float(speeds[-1])
    report["engineering_result_ready"] = (
        report["simple_converged"] and report.get("mass_imbalance_fraction", 1) < 0.01
    )
    rows = []
    iteration = 0
    residuals = {}
    pattern = (
        r"Solving for (\w+), Initial residual = ("
        + NUM
        + r"), Final residual = ("
        + NUM
        + r"), No Iterations (\d+)"
    )
    for line in log.splitlines():
        match = re.match(r"Time = (" + NUM + ")", line)
        if match:
            iteration = float(match[1])
        match = re.search(pattern, line)
        if match:
            field, initial, final, n = match.groups()
            rows.append([iteration, field, float(initial), float(final), int(n)])
            residuals[field] = float(initial)
    report["last_initial_residuals"] = residuals
    out = ROOT / "results"
    out.mkdir(exist_ok=True)
    with (out / "residuals.csv").open("w") as f:
        writer = csv.writer(f)
        writer.writerow(
            ["iteration", "field", "initial_residual", "linear_final_residual", "linear_iterations"]
        )
        writer.writerows(rows)
    (out / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
    status = (
        "Сходимость SIMPLE достигнута; требуется проверка независимости от сетки."
        if report["engineering_result_ready"]
        else "Предварительный расчёт: сходимость и балансы ещё не подтверждены."
    )
    lines = [
        f"# CFD: мангал, {fan_m3_h(params):.3f} м³/ч, нагрев 4 кВт",
        "",
        status,
        "",
        f"Последняя итерация: {report['last_iteration']}",
        f"Нормальное завершение решателя: {report['solver_completed']}",
        "",
    ]
    for label, key, units in [
        ("Расход", "exhaust_m3_h", "м³/ч"),
        ("Мощность нагревателя", "heater_W", "Вт"),
        ("Средняя температура выхода", "outlet_mean_T_K", "K"),
        ("Среднее давление выхода", "outlet_mean_p_Pa", "Па"),
        ("Минимум T", "min_T_K", "K"),
        ("Максимум T", "max_T_K", "K"),
        ("Максимальная скорость", "max_U_m_s", "м/с"),
        ("Поток явного тепла через выход", "outlet_sensible_heat_W", "Вт"),
        ("Поток явного тепла через открытые границы", "ambient_sensible_heat_W", "Вт"),
    ]:
        if key in report:
            lines.append(f"- {label}: {report[key]:.6g} {units}.")
    if "mass_imbalance_fraction" in report:
        lines.append(
            f"- Массовый дисбаланс относительно выхода: {report['mass_imbalance_fraction']:.4%}."
        )
    lines += [
        "",
        "Начальные невязки последней итерации:",
        "",
        "```json",
        json.dumps(residuals, indent=2),
        "```",
        "",
        "Внешнего ветра нет. Вентилятор задан расходом на конце трубы. Дым/CO и горение не моделируются.",
        "Поля несошедшегося расчёта нельзя использовать как оценку достаточности вентилятора.",
        "Расширенная диагностика сетки: logs/checkMesh-extended.log.",
        "Для ParaView: mangal.foam; сечения: postProcessing/flowSections/.",
    ]
    (out / "REPORT.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines[:8]))


if __name__ == "__main__":
    main()
