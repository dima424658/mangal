"""One MPI policy for all supported OpenFOAM stages; default ranks = nproc."""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from model_parameters import mesh_revision

_checked = set()


def ranks(value=None):
    value = str(value if value is not None else os.environ.get("MANGAL_PARALLEL", "nproc"))
    if value.lower() in ("nproc", "auto"):
        executable = shutil.which("nproc")
        if executable:
            result = subprocess.run([executable], text=True, capture_output=True, check=True)
            count = int(result.stdout.strip())
        elif hasattr(os, "sched_getaffinity"):
            count = len(os.sched_getaffinity(0))
        else:
            count = os.cpu_count() or 1
    else:
        count = int(value)
    if count < 1:
        raise ValueError("Parallel process count must be positive")
    return count


def configure(value="nproc"):
    count = ranks(value)
    os.environ["MANGAL_PARALLEL"] = str(count)
    print(f"OpenFOAM execution: {count} MPI process(es) (parallel={value}).", flush=True)
    return count


def launcher(command, count):
    executable = shutil.which("mpirun")
    if not executable:
        raise RuntimeError("mpirun not found; source the OpenFOAM environment")
    version = subprocess.run(
        [executable, "--version"], text=True, capture_output=True, timeout=10
    ).stdout
    flags = ["--use-hwthread-cpus", "--bind-to", "none"] if "Open MPI" in version else []
    return [executable, "-np", str(count), *flags, *command]


def mpi_env():
    env = os.environ.copy()
    env["OMP_NUM_THREADS"] = "1"
    env["OPENBLAS_NUM_THREADS"] = "1"
    return env


def preflight(stage, case, count=None):
    count = ranks() if count is None else count
    if count == 1:
        return
    key = (shutil.which("mpirun"), count)
    if key in _checked:
        return
    try:
        stage(
            "MPI preflight",
            launcher([shutil.which("true") or "/usr/bin/true"], count),
            case / "logs/mpi-preflight.log",
            env=mpi_env(),
        )
    except RuntimeError as error:
        raise RuntimeError(
            "MPI launch failed before changing the case. On this restricted agent host local sockets may be denied; run the same command in your ordinary terminal. Use --parallel 1 only for an intentional serial run.\n"
            + str(error)
        ) from error
    _checked.add(key)


def dictionary(case, count):
    path = case / "system/decomposeParDict"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "FoamFile\n{ version 2.0; format ascii; class dictionary; object decomposeParDict; }\n"
        f"numberOfSubdomains {count};\nmethod scotch;\n"
    )


def time_name(case, selection):
    if selection != "latest":
        return str(selection)
    names = [
        p.name
        for p in case.iterdir()
        if p.is_dir() and re.fullmatch(r"(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?", p.name)
    ]
    return max(names, key=float) if names else "0"


def signature(case, count, selection, fields):
    name = time_name(case, selection)
    files = {}
    if fields and (case / name).is_dir():
        for p in (case / name).iterdir():
            if p.is_file():
                files[p.name] = {"size": p.stat().st_size, "mtime_ns": p.stat().st_mtime_ns}
    zones = case / "constant/polyMesh/cellZones"
    return {
        "ranks": count,
        "mesh": mesh_revision(case),
        "zone_size": zones.stat().st_size if zones.exists() else 0,
        "time": name,
        "fields": files,
        "include_fields": fields,
    }


def remember(case, count, selection="latest", fields=True):
    (case / ".decomposition.json").write_text(
        json.dumps(signature(case, count, selection, fields), indent=2) + "\n"
    )


def normalize_fields(case, selection="latest", processors=False):
    from normalize_mesh import normalize_file

    bases = sorted(case.glob("processor[0-9]*")) if processors else [case]
    for base in bases:
        folder = base / time_name(base, selection)
        if not folder.is_dir():
            continue
        for path in folder.iterdir():
            if not path.is_file() or path.suffix == ".gz":
                continue
            with path.open("rb") as source:
                head = source.read(1024)
            if b"format" not in head or b"binary" in head:
                continue
            backup = case / "validation/field-io" / path.relative_to(case)
            result = normalize_file(
                path, backup.with_suffix(backup.suffix + ".gz"), coordinates_only=False
            )
            if result["normalized_components"]:
                print(
                    f"Normalized {result['normalized_components']} subnormal values in {path}",
                    flush=True,
                )


def decompose(stage, case, count, selection="latest", fields=True):
    cache = case / ".decomposition.json"
    # Interrupted runs may have newer distributed checkpoints than the root case.
    # Recover them before -force can remove processor directories.
    bases = sorted(
        p
        for p in case.glob("processor[0-9]*")
        if p.is_dir() and re.fullmatch(r"processor[0-9]+", p.name)
    )
    if fields and selection == "latest" and bases:
        saved = json.loads(cache.read_text()) if cache.exists() else {}
        times = [time_name(base, "latest") for base in bases]
        if max(map(float, times)) > float(time_name(case, "latest")):
            if len(set(times)) != 1:
                raise RuntimeError(
                    "Processor checkpoints differ; refusing to overwrite distributed results. Inspect processor time folders."
                )
            if saved.get("mesh") != mesh_revision(case):
                raise RuntimeError(
                    "Newer distributed checkpoint has unverified mesh; refusing to overwrite processor directories."
                )
            checkpoint = times[0]
            required = ["U", "p_rgh", "T", "k", "omega", "phi"]
            if not all((base / checkpoint / name).is_file() for base in bases for name in required):
                raise RuntimeError(
                    "Distributed checkpoint is incomplete; refusing to overwrite processor directories."
                )
            print(f"Recover distributed checkpoint {checkpoint} before decomposition.", flush=True)
            normalize_fields(case, checkpoint, processors=True)
            reconstruct(stage, case, checkpoint)
            if len(bases) == count and saved.get("ranks") == count:
                remember(case, count, checkpoint, True)
    desired = signature(case, count, selection, fields)
    if (
        cache.exists()
        and json.loads(cache.read_text()) == desired
        and all((case / f"processor{i}/constant/polyMesh").is_dir() for i in range(count))
    ):
        normalize_fields(case, selection, processors=True)
        return
    normalize_fields(case, selection)
    dictionary(case, count)
    args = ["decomposePar", "-case", str(case), "-force", "-no-sets"]
    args += ["-latestTime"] if selection == "latest" else ["-time", str(selection)]
    if not fields:
        args += ["-no-fields"]
    stage(f"Decompose into {count} domains", args, case / "logs/decomposePar.log")
    remember(case, count, selection, fields)


def reconstruct(stage, case, selection="latest", fields=None):
    args = ["reconstructPar", "-case", str(case)]
    args += ["-latestTime"] if selection == "latest" else ["-time", str(selection)]
    if fields:
        args += ["-fields", "(" + " ".join(fields) + ")"]
    stage("Reconstruct fields", args, case / "logs/reconstructPar.log")


def tool(stage, label, executable, args, case, log, kind="post", selection="latest"):
    """kind: solve, mesh (with fields), post, or field-write (eg cell centres)."""
    case = Path(case).resolve()
    count = ranks()
    if count == 1:
        normalize_fields(case, selection)
        return stage(label, [executable, "-case", str(case), *args], log)
    preflight(stage, case, count)
    decompose(stage, case, count, selection, fields=True)
    stage(
        label,
        launcher([executable, "-case", str(case), *args, "-parallel"], count),
        log,
        env=mpi_env(),
    )
    if kind == "mesh":
        # Own mesh files: reconstructParMesh may reorder and rewrite them.
        for p in (case / "constant/polyMesh").iterdir():
            if p.is_symlink() and p.is_file():
                target = p.resolve()
                p.unlink()
                shutil.copy2(target, p)
        stage(
            "Reconstruct modified mesh",
            ["reconstructParMesh", "-case", str(case), "-constant"],
            case / "logs/reconstructParMesh.log",
        )
        reconstruct(stage, case, selection)
    elif kind == "solve":
        normalize_fields(case, "latest", processors=True)
        reconstruct(stage, case, "latest")
    elif kind == "field-write":
        reconstruct(stage, case, selection, ["C", "Cx", "Cy", "Cz"])
    remember(case, count, "latest" if kind == "solve" else selection, True)


def mesh_pipeline(stage, case, normalize_script):
    count = ranks()
    commands = [
        ("snappyHexMesh", ["-overwrite"]),
        ("topoSet", []),
        ("createPatch", ["-overwrite"]),
        ("checkMesh", ["-allTopology", "-meshQuality"]),
        ("checkMesh-extended", ["-allTopology", "-allGeometry"]),
    ]
    if count == 1:
        stage("snappyHexMesh", ["snappyHexMesh", "-overwrite"], case / "logs/snappyHexMesh.log")
        stage(
            "Normalize mesh",
            [os.sys.executable, str(normalize_script)],
            case / "logs/normalize-mesh.log",
        )
        for name, args in commands[1:]:
            executable = "checkMesh" if name == "checkMesh-extended" else name
            stage(name, [executable, *args], case / "logs" / f"{name}.log")
        return
    preflight(stage, case, count)
    decompose(stage, case, count, "0", fields=False)
    stage(
        "snappyHexMesh",
        launcher(["snappyHexMesh", "-parallel", "-overwrite"], count),
        case / "logs/snappyHexMesh.log",
        env=mpi_env(),
    )
    stage(
        "Normalize processor meshes",
        [os.sys.executable, str(normalize_script), "--case", str(case), "--processors"],
        case / "logs/normalize-mesh.log",
    )
    for name, args in commands[1:]:
        executable = "checkMesh" if name == "checkMesh-extended" else name
        stage(
            name,
            launcher([executable, "-parallel", *args], count),
            case / "logs" / f"{name}.log",
            env=mpi_env(),
        )
    stage(
        "Reconstruct mesh",
        ["reconstructParMesh", "-constant"],
        case / "logs/reconstructParMesh.log",
    )
    stage(
        "Normalize reconstructed mesh",
        [os.sys.executable, str(normalize_script), "--case", str(case)],
        case / "logs/normalize-reconstructed.log",
    )
    (case / ".decomposition.json").unlink(missing_ok=True)
