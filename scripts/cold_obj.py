#!/usr/bin/env python3
"""Offline Blender preview: actual OpenFOAM streamlines as coloured tube meshes."""

import copy
import json
import math
from pathlib import Path

from charcoal import lumps
from cold_viewer import ROOT, read_vtp, solid
from generate import grill, hood
from model_parameters import fan_m3_h

COLORS = [
    (0.086, 0.302, 0.70),
    (0.05, 0.52, 0.82),
    (0.06, 0.75, 0.82),
    (0.20, 0.79, 0.65),
    (0.50, 0.82, 0.38),
    (0.85, 0.82, 0.25),
    (0.96, 0.62, 0.20),
    (0.94, 0.29, 0.18),
]


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def unit(a):
    n = math.hypot(*a)
    return tuple(v / n for v in a) if n > 1e-12 else (0, 0, 1)


def add(a, b, scale=1):
    return tuple(a[i] + scale * b[i] for i in range(3))


def export_obj(
    out=None,
    params=None,
    scalar="speed",
    color_range=(0, 0.6),
    with_charcoal=False,
    basename="cold-flow",
):
    out = Path(out) if out else ROOT / "visualization/cold"
    p = (
        params
        if params is not None
        else json.loads((ROOT / "validation/isothermal/geometry/parameters.json").read_text())
    )
    p = copy.deepcopy(p)
    p["assumptions"]["duct_mesh_extension_m"] = 0
    materials = "newmtl steel\nKd 0.48 0.56 0.64\nd 0.35\nNs 50\nnewmtl charcoal\nKd 0.055 0.05 0.045\nNs 8\n"
    for i, color in enumerate(COLORS):
        materials += f"newmtl {scalar}_{i}\nKd " + " ".join(map(str, color)) + "\nNs 20\n"
    (out / (basename + ".mtl")).write_text(materials)
    count = 0
    total_tracks = 0
    faces = 0
    with (out / (basename + ".obj")).open("w") as f:
        f.write(
            f"# OpenFOAM streamlines. Metres. {fan_m3_h(p):.6f} м³/ч.\n# Colour field {scalar}; range {color_range}; values above range saturate red.\nmtllib {basename}.mtl\n"
        )

        def vertex(pt):
            nonlocal count
            f.write("v " + " ".join(f"{v:.8g}" for v in pt) + "\n")
            count += 1
            return count

        def face(ids):
            nonlocal faces
            f.write("f " + " ".join(map(str, ids)) + "\n")
            faces += 1

        from skewers import shapes as skewer_shapes

        for name, shape in [("Grill", grill(p)), ("Hood", hood(p)[0])] + [
            (f"Skewer_{i + 1}", shape) for i, shape in enumerate(skewer_shapes(p))
        ]:
            f.write(f"o {name}\nusemtl steel\n")
            data = solid(shape)
            start = count
            for j in range(0, len(data["points"]), 3):
                vertex(data["points"][j : j + 3])
            cells = data["polys"]
            j = 0
            while j < len(cells):
                n = cells[j]
                face([start + 1 + i for i in cells[j + 1 : j + 1 + n]])
                j += n + 1
        if with_charcoal:
            f.write("o Charcoal_bed\nusemtl charcoal\n")
            for vertices, triangles in lumps(p):
                start = count
                for pt in vertices:
                    vertex(pt)
                for tri in triangles:
                    face([start + i + 1 for i in tri])
        sources = ["intakeStreamlines", "grillStreamlines"]
        if (out / "streamlines/holeStreamlines.vtp").exists():
            sources.append("holeStreamlines")
        for source in sources:
            radius = 0.0008 if source == "holeStreamlines" else 0.0015
            spacing = 0.01 if source == "holeStreamlines" else 0.023
            data = read_vtp(out / f"streamlines/{source}.vtp")
            points = [tuple(data["points"][i : i + 3]) for i in range(0, len(data["points"]), 3)]
            lines = data["lines"]
            offset = 0
            track = 0
            while offset < len(lines):
                n = lines[offset]
                ids = lines[offset + 1 : offset + 1 + n]
                offset += n + 1
                chosen = [ids[0]]
                for index in ids[1:]:
                    if math.dist(points[chosen[-1]], points[index]) >= spacing:
                        chosen.append(index)
                if chosen[-1] != ids[-1]:
                    chosen.append(ids[-1])
                if len(chosen) < 2:
                    continue
                f.write(f"o {source}_{track:02d}\n")
                track += 1
                total_tracks += 1
                rings = []
                tangents = []
                for i, index in enumerate(chosen):
                    before = points[chosen[max(i - 1, 0)]]
                    after = points[chosen[min(i + 1, len(chosen) - 1)]]
                    tangent = unit(tuple(after[k] - before[k] for k in range(3)))
                    tangents.append(tangent)
                    ref = (0, 0, 1) if abs(tangent[2]) < 0.9 else (1, 0, 0)
                    u = unit(cross(tangent, ref))
                    v = cross(tangent, u)
                    ring = []
                    for side in range(6):
                        angle = 2 * math.pi * side / 6
                        pt = add(
                            add(points[index], u, radius * math.cos(angle)),
                            v,
                            radius * math.sin(angle),
                        )
                        ring.append(vertex(pt))
                    rings.append(ring)
                for i in range(len(rings) - 1):
                    value = data[scalar][chosen[i]]
                    color = max(
                        0,
                        min(
                            7, int((value - color_range[0]) / (color_range[1] - color_range[0]) * 8)
                        ),
                    )
                    f.write(f"usemtl {scalar}_{color}\n")
                    for side in range(6):
                        face(
                            [
                                rings[i][side],
                                rings[i][(side + 1) % 6],
                                rings[i + 1][(side + 1) % 6],
                                rings[i + 1][side],
                            ]
                        )
                face(list(reversed(rings[0])))
                face(rings[-1])
                # Arrows indicate the integrated forward direction, not transient timing.
                for i in range(10, len(chosen) - 1, 25):
                    value = data[scalar][chosen[i]]
                    color = max(
                        0,
                        min(
                            7, int((value - color_range[0]) / (color_range[1] - color_range[0]) * 8)
                        ),
                    )
                    f.write(f"usemtl {scalar}_{color}\n")
                    center = points[chosen[i]]
                    tangent = tangents[i]
                    ref = (0, 0, 1) if abs(tangent[2]) < 0.9 else (1, 0, 0)
                    u = unit(cross(tangent, ref))
                    v = cross(tangent, u)
                    tip = vertex(add(center, tangent, radius * 13.5))
                    base = []
                    for side in range(6):
                        angle = 2 * math.pi * side / 6
                        base.append(
                            vertex(
                                add(
                                    add(center, u, radius * 4 * math.cos(angle)),
                                    v,
                                    radius * 4 * math.sin(angle),
                                )
                            )
                        )
                    for side in range(6):
                        face([base[side], base[(side + 1) % 6], tip])
                    face(list(reversed(base)))
    print(
        json.dumps(
            {
                "obj_vertices": count,
                "obj_faces": faces,
                "tracks": total_tracks,
                "tube_radius_m": {"holes": 0.0008, "others": 0.0015},
                "color_field": scalar,
                "color_range": color_range,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    export_obj()
