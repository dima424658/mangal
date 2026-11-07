#!/usr/bin/env python3
"""Visible, single-owner OpenFOAM workflow with process-group cancellation."""

import argparse
import json
import os
import re
import selectors
import shutil
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from model_parameters import fan_cfm, fan_m3_s, mesh_signature

ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def case_lock():
    # Advisory lock is released by the OS even on interruption; no stale PID locks.
    import fcntl

    path = ROOT / ".case.lock"
    with path.open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock.seek(0)
            raise RuntimeError(
                "Another mangal mesh/run/check owns this case: " + lock.read().strip()
            )
        lock.seek(0)
        lock.truncate()
        lock.write(f"PID {os.getpid()} {' '.join(sys.argv[1:])}\n")
        lock.flush()
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def stage(label, command, logfile, cwd=ROOT, env=None):
    logfile = Path(logfile)
    logfile.parent.mkdir(parents=True, exist_ok=True)
    print(
        f"[{time.strftime('%H:%M:%S')}] {label}: started; log {logfile.relative_to(ROOT)}",
        flush=True,
    )
    start = time.monotonic()
    last_update = start
    last_line = "starting"
    partial = b""
    if logfile.exists():
        history = logfile.parent / "history"
        history.mkdir(exist_ok=True)
        shutil.move(str(logfile), str(history / f"{time.time_ns()}-{logfile.name}"))
    with logfile.open("wb") as output:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=env,
        )
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        try:
            while selector.get_map():
                for key, _ in selector.select(timeout=1):
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    output.write(chunk)
                    output.flush()
                    lines = (partial + chunk).split(b"\n")
                    partial = lines.pop()
                    for line in lines:
                        text = line.decode(errors="replace").strip()
                        if text:
                            last_line = text
                            if re.match(r"Time = \d+$", text):
                                print(f"  {text}", flush=True)
                now = time.monotonic()
                if now - last_update >= 15:
                    print(f"  {label}: {now - start:.0f}s; {last_line[:180]}", flush=True)
                    last_update = now
            code = process.wait()
        except BaseException:
            print(f"  Stopping {label}...", flush=True)
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            raise
        finally:
            selector.close()
            process.stdout.close()
    if code:
        tail = "\n".join(logfile.read_text(errors="replace").splitlines()[-24:])
        raise RuntimeError(f"{label} failed (exit {code}).\n{tail}")
    print(
        f"[{time.strftime('%H:%M:%S')}] {label}: finished in {time.monotonic() - start:.1f}s",
        flush=True,
    )


def preserve_mesh():
    """Retain historical case links before replacing the root mesh."""
    mesh_path = ROOT / "constant/polyMesh"
    if not mesh_path.exists():
        return
    archive = ROOT / "validation/history" / f"polyMesh-{time.time_ns()}"
    links = []
    for base in [ROOT / "validation", ROOT / "runs"]:
        if not base.exists():
            continue
        for directory, dirs, files in os.walk(base, followlinks=False):
            for name in dirs + files:
                path = Path(directory) / name
                if path.is_symlink():
                    target = path.resolve()
                    if target == mesh_path or mesh_path in target.parents:
                        links.append((path, target.relative_to(mesh_path)))
    archive.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(mesh_path), str(archive))
    for path, relative in links:
        path.unlink()
        path.symlink_to(archive / relative, target_is_directory=(archive / relative).is_dir())
    (ROOT / ".decomposition.json").unlink(missing_ok=True)
    print(f"Previous mesh preserved in {archive.relative_to(ROOT)}", flush=True)


def mesh(coarse):
    preserve_mesh()
    logs = ROOT / "logs"
    stage(
        "Geometry",
        [sys.executable, "scripts/generate.py"] + (["--coarse"] if coarse else []),
        logs / "generate.log",
    )
    for name in ["grill", "hood", "skewers"]:
        stage(
            "surfaceCheck " + name,
            ["surfaceCheck", f"constant/triSurface/{name}.stl"],
            logs / f"surfaceCheck-{name}.log",
        )
    stage("blockMesh", ["blockMesh"], logs / "blockMesh.log")
    from parallel import mesh_pipeline

    mesh_pipeline(stage, ROOT, ROOT / "scripts/normalize_mesh.py")
    finish_mesh(already_processed=True)


def finish_mesh(already_processed=False):
    logs = ROOT / "logs"
    if "Finished meshing without any errors" not in (logs / "snappyHexMesh.log").read_text():
        raise RuntimeError("A successfully snapped mesh is required")
    if not already_processed:
        from parallel import tool

        stage(
            "Normalize tiny coordinates",
            [sys.executable, "scripts/normalize_mesh.py"],
            logs / "normalize-mesh.log",
        )
        for label, app, args in [
            ("topoSet", "topoSet", []),
            ("createPatch", "createPatch", ["-overwrite"]),
            ("checkMesh", "checkMesh", ["-allTopology", "-meshQuality"]),
            ("checkMesh-extended", "checkMesh", ["-allTopology", "-allGeometry"]),
        ]:
            tool(
                stage,
                label,
                app,
                args,
                ROOT,
                logs / (label + ".log"),
                kind="mesh" if app in ("topoSet", "createPatch") else "post",
                selection="0",
            )
    check = (logs / "checkMesh.log").read_text()
    if "Mesh OK." not in check:
        raise RuntimeError("Mesh did not pass mandatory checks; see logs/checkMesh.log")
    (ROOT / "geometry/MESH_REBUILD_REQUIRED").unlink(missing_ok=True)
    try:
        stage(
            "Validate mesh",
            [sys.executable, "scripts/validate.py", "--mesh"],
            logs / "validate-mesh.log",
        )
    except BaseException:
        (ROOT / "geometry/MESH_REBUILD_REQUIRED").touch()
        raise
    # Fingerprint the exact model used for the mesh, not just its patch names.
    shutil.copy2(
        ROOT / "geometry/parameters.json", ROOT / "constant/polyMesh/model-parameters.json"
    )


def current_mesh():
    stamp = ROOT / "constant/polyMesh/model-parameters.json"
    return (
        (ROOT / "constant/polyMesh/boundary").exists()
        and not (ROOT / "geometry/MESH_REBUILD_REQUIRED").exists()
        and stamp.exists()
        and mesh_signature(json.loads(stamp.read_text()))
        == mesh_signature(json.loads((ROOT / "geometry/parameters.json").read_text()))
    )


def smoke():
    stage(
        "Validate mesh",
        [sys.executable, "scripts/validate.py", "--mesh"],
        ROOT / "logs/validate-mesh.log",
    )
    case = ROOT / "validation/smoke"
    if case.exists():
        shutil.rmtree(case)
    case.mkdir(parents=True)
    for name in ["0", "system", "geometry"]:
        shutil.copytree(ROOT / name, case / name)
    (case / "constant").mkdir()
    for item in (ROOT / "constant").iterdir():
        if item.is_file():
            shutil.copy2(item, case / "constant" / item.name)
        elif item.name == "polyMesh":
            (case / "constant" / item.name).symlink_to(item, target_is_directory=True)
    (case / "logs").mkdir()
    for name in ["checkMesh.log", "checkMesh-extended.log"]:
        shutil.copy2(ROOT / "logs" / name, case / "logs" / name)
    text = (case / "system/controlDict").read_text()
    text = re.sub(r"\bendTime\s+[^;]+;", "endTime 5;", text)
    text = re.sub(r"^writeInterval\s+[^;]+;", "writeInterval 5;", text, flags=re.M)
    (case / "system/controlDict").write_text(text)
    from parallel import tool

    tool(
        stage,
        "Smoke solver",
        "buoyantSimpleFoam",
        [],
        case,
        case / "logs/buoyantSimpleFoam.log",
        kind="solve",
        selection="0",
    )
    stage(
        "Validate smoke",
        [sys.executable, "scripts/validate.py", "--case", str(case), "--result"],
        case / "logs/validate.log",
    )
    print((case / "logs/validate.log").read_text(), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["mesh", "finish-mesh", "run", "check"])
    parser.add_argument(
        "--parallel",
        nargs="?",
        const="nproc",
        default="nproc",
        help="MPI ranks; defaults to nproc. Use 1 for explicit serial execution.",
    )
    parser.add_argument("--coarse", action="store_true")
    parser.add_argument(
        "--resume", action="store_true", help="Resume the latest charcoal checkpoint"
    )
    parser.add_argument(
        "--cold",
        action="store_true",
        help="Run isothermal ventilation with simpleFoam (no heating)",
    )
    parser.add_argument(
        "--iterations", type=int, help="Override maximum SIMPLE iterations for this run"
    )
    args = parser.parse_args()
    os.chdir(ROOT)

    def terminate(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, terminate)
    try:
        with case_lock():
            from parallel import configure, preflight

            count = configure(args.parallel)
            preflight(stage, ROOT, count)
            if args.action == "mesh":
                mesh(args.coarse)
            elif args.action == "finish-mesh":
                finish_mesh()
            elif args.action == "check":
                if not current_mesh():
                    raise RuntimeError(
                        "Mesh is stale or missing. Run ./mangal mesh --coarse first."
                    )
                smoke()
            else:
                if not current_mesh():
                    print("Mesh is stale or missing; rebuilding it first.", flush=True)
                    mesh(args.coarse)
                if args.cold:
                    from isothermal_start import main as run_cold

                    run_cold(already_locked=True, iterations=args.iterations or 30)
                    return 0
                if (
                    json.loads((ROOT / "geometry/parameters.json").read_text())
                    .get("charcoal", {})
                    .get("enabled")
                ):
                    from prepare_charcoal import run as run_charcoal

                    run_charcoal(iterations=args.iterations or 120, resume=args.resume)
                    return 0
                if args.iterations:
                    if args.iterations <= 0:
                        raise RuntimeError("Iteration count must be positive")
                    path = ROOT / "system/controlDict"
                    text = path.read_text()
                    path.write_text(
                        re.sub(r"\bendTime\s+[^;]+;", f"endTime {args.iterations};", text)
                    )
                u_path = ROOT / "0/U"
                text = u_path.read_text()
                text = re.sub(
                    r"(volumetricFlowRate\s+constant\s+)[^;]+;",
                    lambda m: (
                        m[1]
                        + f"{fan_m3_s(json.loads((ROOT / 'geometry/parameters.json').read_text())):.14g};"
                    ),
                    text,
                )
                u_path.write_text(text)
                stage(
                    "Validate mesh",
                    [sys.executable, "scripts/validate.py", "--mesh"],
                    ROOT / "logs/validate-mesh.log",
                )
                previous = ROOT / "postProcessing"
                if previous.exists():
                    history = ROOT / "validation/history"
                    history.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(previous), str(history / f"postProcessing-{time.time_ns()}"))
                print(
                    f"Fan target: {fan_cfm(json.loads((ROOT / 'geometry/parameters.json').read_text())):.3f} CFM",
                    flush=True,
                )
                from parallel import tool

                tool(
                    stage,
                    "buoyantSimpleFoam",
                    "buoyantSimpleFoam",
                    [],
                    ROOT,
                    ROOT / "logs/buoyantSimpleFoam.log",
                    kind="solve",
                    selection="0",
                )
                stage(
                    "Validate result",
                    [sys.executable, "scripts/validate.py", "--result"],
                    ROOT / "logs/validate-result.log",
                )
                print((ROOT / "logs/validate-result.log").read_text(), flush=True)
                stage(
                    "Result summary",
                    [sys.executable, "scripts/summarize.py"],
                    ROOT / "logs/summary.log",
                )
                (ROOT / "mangal.foam").touch()
    except KeyboardInterrupt:
        print("Stopped by user; lock released.", file=sys.stderr)
        return 130
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
