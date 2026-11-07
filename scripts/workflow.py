#!/usr/bin/env python3
"""Single entry point for the existing OpenFOAM and visualization workflows."""

import argparse
import json
import shlex
import signal
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable


def parser():
    p = argparse.ArgumentParser(
        prog="./mangal",
        description="Мангал: расчёт, preview и visualization. MPI по умолчанию = nproc.",
    )
    sub = p.add_subparsers(dest="action", required=True)
    for name, help_text in [
        ("all", "Расчёт → OBJ preview → отверстия → visualization"),
        ("mesh", "Построить и проверить сетку"),
        ("run", "Новый расчёт"),
        ("resume", "Продолжить сохранённый тепловой расчёт"),
        ("visualize", "Обновить HTML, OBJ и ParaView по сохранённым полям"),
        ("preview", "Собрать только OBJ геометрии, без OpenFOAM"),
        ("audit", "Измерить потоки через восемь отверстий"),
        ("check", "Короткий проверочный CFD-прогон"),
        ("report", "Обновить отчёт по сохранённому тепловому результату"),
        ("status", "Показать сохранённые итерации и состояние сетки"),
    ]:
        s = sub.add_parser(name, help=help_text)
        s.add_argument(
            "--dry-run", action="store_true", help="Показать команды без запуска и изменения файлов"
        )
        if name in ("all", "mesh", "run", "resume", "visualize", "audit", "check"):
            s.add_argument(
                "--parallel",
                nargs="?",
                const="nproc",
                default="nproc",
                help="Число MPI-процессов, nproc или 1",
            )
        if name in ("all", "run", "resume"):
            s.add_argument(
                "--iterations",
                type=int,
                help="Конечная итерация SIMPLE, не количество дополнительных итераций",
            )
        if name in ("all", "mesh", "run"):
            s.add_argument("--coarse", action="store_true")
        if name in ("all", "run"):
            s.add_argument(
                "--resume", action="store_true", help="Продолжить тепловой расчёт вместо нового"
            )
        if name in ("all", "run", "visualize", "audit"):
            s.add_argument(
                "--cold", action="store_true", help="Холодный case вместо расчёта с углями"
            )
        if name == "all":
            s.add_argument(
                "--rebuild-mesh",
                action="store_true",
                help="Принудительно перестроить сетку перед новым расчётом",
            )
    return p


def plan(args):
    action = args.action
    if getattr(args, "iterations", None) is not None and args.iterations <= 0:
        raise ValueError("--iterations must be positive")
    resume = action == "resume" or getattr(args, "resume", False)
    cold = getattr(args, "cold", False)
    if resume and cold:
        raise ValueError("--resume cannot be combined with --cold")
    if resume and getattr(args, "rebuild_mesh", False):
        raise ValueError(
            "Resume requires the saved mesh; do not combine --resume and --rebuild-mesh"
        )
    parallel = getattr(args, "parallel", "nproc")
    if parallel not in ("nproc", "auto") and (not parallel.isdecimal() or int(parallel) < 1):
        raise ValueError("--parallel must be nproc, auto or a positive integer")
    mpi = ["--parallel", parallel]
    mesh = [PYTHON, "scripts/run_case.py", "mesh", *mpi] + (
        ["--coarse"] if getattr(args, "coarse", False) else []
    )
    run = (
        [PYTHON, "scripts/run_case.py", "run", *mpi]
        + (["--resume"] if resume else [])
        + (["--cold"] if cold else [])
    )
    if getattr(args, "coarse", False):
        run += ["--coarse"]
    if getattr(args, "iterations", None) is not None:
        run += ["--iterations", str(args.iterations)]
    preview = [PYTHON, "scripts/export_obj.py"]
    params = json.loads((ROOT / "geometry/parameters.json").read_text())
    if (
        action in ("all", "visualize")
        and not cold
        and not params.get("charcoal", {}).get("enabled")
    ):
        raise ValueError(
            "Heated visualization requires charcoal.enabled; use --cold for the cold case"
        )
    visualize = [PYTHON, "scripts/visualize_cold.py", *mpi] + ([] if cold else ["--charcoal"])
    case = "validation/isothermal" if cold else "runs/charcoal"
    audit = [PYTHON, "scripts/audit_holes.py", *mpi, "--case", case]
    if action == "all":
        return ([mesh] if args.rebuild_mesh else []) + [run, preview, audit, visualize]
    return {
        "mesh": [mesh],
        "run": [run],
        "resume": [run],
        "preview": [preview],
        "visualize": [visualize],
        "audit": [audit],
        "check": [[PYTHON, "scripts/run_case.py", "check", *mpi]],
        "report": [[PYTHON, "scripts/charcoal_report.py"]],
        "status": [],
    }[action]


def status():
    from parallel import time_name
    from run_case import current_mesh

    print("Mesh:", "current" if current_mesh() else "missing or requires rebuild")
    for name in ["validation/isothermal", "runs/charcoal"]:
        case = ROOT / name
        if not case.exists():
            continue
        root_time = time_name(case, "latest")
        distributed = {
            p.name: time_name(p, "latest")
            for p in sorted(case.glob("processor[0-9]*"))
            if p.is_dir()
        }
        print(f"{name}: saved fields {root_time}")
        if distributed:
            print("  MPI checkpoints:", json.dumps(distributed))
    print("Geometry OBJ: preview/mangal.obj")
    print(
        "Heated views: visualization/charcoal/charcoal-flow.html, charcoal-flow.obj, charcoal-view.vtm"
    )


def execute(commands):
    for index, command in enumerate(commands, 1):
        print(f"[{index}/{len(commands)}] {shlex.join(command)}", flush=True)
        child = subprocess.Popen(command, cwd=ROOT)
        try:
            code = child.wait()
        except KeyboardInterrupt:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
            raise
        if code:
            raise RuntimeError(f"Stage exited with code {code}; subsequent stages were not started")


def interrupt(signum, frame):
    raise KeyboardInterrupt


def main(argv=None):
    p = parser()
    args = p.parse_args(argv)
    try:
        commands = plan(args)
        if args.dry_run:
            for command in commands:
                print(shlex.join(command))
            if args.action == "status":
                print("Read-only status inspection")
            return 0
        if args.action == "status":
            status()
            return 0
        signal.signal(signal.SIGTERM, interrupt)
        execute(commands)
        print("Done.", flush=True)
    except KeyboardInterrupt:
        print("Stopped; subsequent stages were not started.", file=sys.stderr)
        return 130
    except (ValueError, RuntimeError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
