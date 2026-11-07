#!/usr/bin/env python3
"""Avoid OpenFOAM strtod ERANGE on subnormal near-zero written coordinates.

Only magnitudes <=1e-300 m are changed; ordinary coordinates are untouched.
"""

import argparse
import gzip
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def normalize_file(path, backup, coordinates_only=True):
    temporary = path.with_name("points.normalized.tmp")
    pattern = re.compile(r"[-+]?\d+(?:\.\d*)?[eE]-\d{3,}")
    count = 0
    largest = 0.0

    def normalize(match):
        nonlocal count, largest
        value = float(match[0])
        if abs(value) <= 1e-300:
            count += 1
            largest = max(largest, abs(value))
            return "0"
        return match[0]

    try:
        with path.open() as source, temporary.open("w") as dest:
            for line in source:
                if (not coordinates_only or line.startswith("(")) and (
                    "e-" in line or "E-" in line
                ):
                    line = pattern.sub(normalize, line)
                dest.write(line)
        if count:
            backup.parent.mkdir(parents=True, exist_ok=True)
            with path.open("rb") as source, gzip.open(backup, "wb") as dest:
                import shutil

                shutil.copyfileobj(source, dest)
            temporary.replace(path)
        else:
            temporary.unlink()
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "path": str(path),
        "normalized_components": count,
        "largest_change_m": largest,
        "threshold_m": 1e-300,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", type=Path, default=ROOT)
    parser.add_argument("--processors", action="store_true")
    args = parser.parse_args()
    case = args.case.resolve()
    paths = (
        sorted(case.glob("processor[0-9]*/constant/polyMesh/points"))
        if args.processors
        else [case / "constant/polyMesh/points"]
    )
    if not paths:
        raise RuntimeError("No processor mesh points found")
    results = []
    for path in paths:
        suffix = path.relative_to(case).parts[0] if args.processors else "root"
        backup = (
            case
            / "validation/mesh-io"
            / (
                "points-before-normalization.gz"
                if suffix == "root"
                else suffix + "-points-before-normalization.gz"
            )
        )
        results.append(normalize_file(path, backup))
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
