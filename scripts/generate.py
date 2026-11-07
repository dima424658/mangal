#!/usr/bin/env python3
"""Generate closed manifold STL solids and OpenFOAM dictionaries (standard library)."""

import argparse
import collections
import json
import math
from pathlib import Path

from model_parameters import fan_m3_s

ROOT = Path(__file__).resolve().parents[1]


def header(obj, cls="dictionary"):
    return f"FoamFile\n{{ version 2.0; format ascii; class {cls}; object {obj}; }}\n"


def write(path, body, cls="dictionary"):
    path = ROOT / path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(header(path.name, cls) + body)


def perimeter(a, b, n=64):
    # Equal subdivisions of each rectangle edge, starting at lower left.
    corners = [(-a, -b), (a, -b), (a, b), (-a, b)]
    return [
        (
            corners[k][0] * (1 - t) + corners[(k + 1) % 4][0] * t,
            corners[k][1] * (1 - t) + corners[(k + 1) % 4][1] * t,
        )
        for k in range(4)
        for t in (j / (n // 4) for j in range(n // 4))
    ]


class Surface:
    def __init__(self):
        self.faces = []

    def tri(self, a, b, c, region):
        self.faces.append((tuple(a), tuple(b), tuple(c), region))

    def strip(self, a, b, region, closed=True):
        for i in range(len(a) if closed else len(a) - 1):
            j = (i + 1) % len(a)
            self.tri(a[i], a[j], b[j], region)
            self.tri(a[i], b[j], b[i], region)

    def fan(self, points, region):
        c = tuple(sum(p[k] for p in points) / len(points) for k in range(3))
        for i in range(len(points)):
            self.tri(c, points[i], points[(i + 1) % len(points)], region)

    def validated_regions(self):
        # Weld vertices, verify exactly two incident faces per edge, orient by BFS.
        ids = {}
        vertices = []
        faces = []
        for *pts, region in self.faces:
            indices = []
            for p in pts:
                key = tuple(round(x, 10) for x in p)
                if key not in ids:
                    ids[key] = len(vertices)
                    vertices.append(key)
                indices.append(ids[key])
            assert len(set(indices)) == 3, "degenerate triangle"
            faces.append((indices, region))
        edges = collections.defaultdict(list)
        for i, (v, _) in enumerate(faces):
            for a, b in zip(v, v[1:] + v[:1]):
                edges[tuple(sorted((a, b)))].append((i, 1 if a < b else -1))
        bad = [(k, v) for k, v in edges.items() if len(v) != 2]
        assert not bad, f"Non-manifold edges: {bad[:5]}"
        adj = collections.defaultdict(list)
        for pair in edges.values():
            (i, a), (j, b) = pair
            adj[i].append((j, -a * b))
            adj[j].append((i, -a * b))
        signs = {0: 1}
        todo = [0]
        while todo:
            i = todo.pop()
            for j, rel in adj[i]:
                s = signs[i] * rel
                if j in signs:
                    assert signs[j] == s
                else:
                    signs[j] = s
                    todo.append(j)
        assert len(signs) == len(faces), "Disconnected surface"

        def cross(a, b):
            return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])

        volume = 0
        for i, (v, _) in enumerate(faces):
            a, b, c = (vertices[j] for j in v)
            volume += signs[i] * sum(x * y for x, y in zip(a, cross(b, c))) / 6
        assert abs(volume) > 1e-10
        regions = collections.defaultdict(list)
        for i, (v, r) in enumerate(faces):
            if signs[i] * (1 if volume > 0 else -1) < 0:
                v = [v[0], v[2], v[1]]
            regions[r].append([vertices[j] for j in v])
        return regions, {
            "triangles": len(faces),
            "vertices": len(vertices),
            "solid_volume_m3": abs(volume),
        }

    def save(self, path):
        regions, report = self.validated_regions()

        def cross(a, b):
            return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])

        with path.open("w") as f:
            for region, tris in regions.items():
                f.write(f"solid {region}\n")
                for a, b, c in tris:
                    normal = cross(
                        tuple(b[k] - a[k] for k in range(3)), tuple(c[k] - a[k] for k in range(3))
                    )
                    norm = math.sqrt(sum(x * x for x in normal))
                    assert norm > 1e-14
                    f.write(
                        " facet normal "
                        + " ".join(f"{x / norm:.12g}" for x in normal)
                        + "\n  outer loop\n"
                    )
                    for p in (a, b, c):
                        f.write("   vertex " + " ".join(f"{x:.12g}" for x in p) + "\n")
                    f.write("  endloop\n endfacet\n")
                f.write(f"endsolid {region}\n")
        return report


def grill(p):
    g = p["grill"]
    t = p["assumptions"]["wall_thickness_m"]
    w, d, h = g["width"] / 2, g["depth"] / 2, g["height"]
    s = Surface()
    boundaries = []
    for inset in (False, True):
        a, b, z0 = (w - t, d - t, t) if inset else (w, d, 0)
        top = []
        bottom = []
        # Four hole tiles plus end tiles; matching edge subdivisions prevent T junctions.
        xs = (
            [-a]
            + [g["hole_centers_x_assumed"][0] - 0.085]
            + [x + 0.085 for x in g["hole_centers_x_assumed"]]
            + [a]
        )
        for side in (-1, 1):
            y = side * b
            for i, (lo, hi) in enumerate(zip(xs, xs[1:])):
                cx = (lo + hi) / 2
                cz = (z0 + h) / 2
                rect = [(cx + x, y, cz + z) for x, z in perimeter((hi - lo) / 2, (h - z0) / 2)]
                for point in rect:
                    if abs(point[2] - h) < 1e-9:
                        top.append(point)
                    if abs(point[2] - z0) < 1e-9:
                        bottom.append(point)
                if 1 <= i <= 4:
                    circle = []
                    for u, v in perimeter(1, 1):
                        r = g["hole_diameter"] / 2 / math.hypot(u, v)
                        circle.append(
                            (
                                g["hole_centers_x_assumed"][i - 1] + r * u,
                                y,
                                g["hole_center_z_assumed"] + r * v,
                            )
                        )
                    s.strip(rect, circle, "grillWalls")
                    if not inset:
                        inner = [(x, side * (d - t), z) for x, _, z in circle]
                        s.strip(circle, inner, "grillWalls")
                else:
                    s.fan(rect, "grillWalls")
        for side in (-1, 1):
            rect = [(side * a, y, (h + z0) / 2 + z) for y, z in perimeter(b, (h - z0) / 2)]
            s.fan(rect, "grillWalls")
            top.extend(pt for pt in rect if abs(pt[2] - h) < 1e-9)
            bottom.extend(pt for pt in rect if abs(pt[2] - z0) < 1e-9)

        # Order perimeter points around rectangle; exact duplicate corner removal.
        def order(pts):
            return sorted(
                set(tuple(round(x, 10) for x in pt) for pt in pts),
                key=lambda pt: math.atan2(pt[1] / b, pt[0] / a),
            )

        top = order(top)
        bottom = order(bottom)
        s.fan(bottom, "heater" if inset else "grillWalls")
        boundaries.append(top)
    # Top rim: merge differing angular discretizations with a zipper triangulation.
    outer, inner = boundaries

    def param(pt, a, b):
        x, y, _ = pt
        if abs(y + b) < 1e-8:
            return (x + a) / (2 * a)
        if abs(x - a) < 1e-8:
            return 1 + (y + b) / (2 * b)
        if abs(y - b) < 1e-8:
            return 2 + (a - x) / (2 * a)
        return 3 + (b - y) / (2 * b)

    outer = sorted(outer, key=lambda q: param(q, w, d))
    inner = sorted(inner, key=lambda q: param(q, w - t, d - t))
    i = j = 0
    while i < len(outer) or j < len(inner):
        ai = param(outer[(i + 1) % len(outer)], w, d) if i + 1 < len(outer) else 4
        bj = param(inner[(j + 1) % len(inner)], w - t, d - t) if j + 1 < len(inner) else 4
        if i < len(outer) and (j == len(inner) or ai <= bj):
            s.tri(
                outer[i % len(outer)],
                outer[(i + 1) % len(outer)],
                inner[j % len(inner)],
                "grillWalls",
            )
            i += 1
        else:
            s.tri(
                outer[i % len(outer)],
                inner[(j + 1) % len(inner)],
                inner[j % len(inner)],
                "grillWalls",
            )
            j += 1
    dx = g.get("offset_x_m", 0.0)
    dy = g.get("offset_y_m", 0.0)
    s.faces = [
        (*(tuple((point[0] + dx, point[1] + dy, point[2])) for point in (a, b, c)), region)
        for a, b, c, region in s.faces
    ]
    return s


def hood(p):
    h = p["hood"]
    d = p["duct"]
    a = p["assumptions"]
    t = a["wall_thickness_m"]
    z = h["top_z"]
    z1 = z + a["square_to_round_length_m"]
    z2 = z1 + d["first_length"]
    z3 = z2 + a["reducer_length_m"]
    top = z3 + d["second_length"]
    profiles = [
        ("rect", h["inlet_width"] / 2, h["inlet_depth"] / 2, h["bottom_z"]),
        ("rect", h["top_width"] / 2, h["top_depth"] / 2, z),
        ("circle", d["first_diameter"] / 2, 0, z1),
        ("circle", d["first_diameter"] / 2, 0, z2),
        ("circle", d["second_diameter"] / 2, 0, z3),
        ("circle", d["second_diameter"] / 2, 0, top + a.get("duct_mesh_extension_m", 0.04)),
    ]
    s = Surface()
    shells = []
    for offset in (0, t):
        rings = []
        for kind, x, y, z in profiles:
            ring = []
            for u, v in perimeter(1, 1):
                if kind == "rect":
                    ring.append((u * (x + offset), v * (y + offset), z))
                else:
                    r = (x + offset) / math.hypot(u, v)
                    ring.append((u * r, v * r, z))
            rings.append(ring)
        for lower, upper in zip(rings, rings[1:]):
            s.strip(lower, upper, "hoodWalls")
        shells.append(rings)
    if h.get("enclosure"):
        enclosure = h["enclosure"]
        assert all(enclosure[k] for k in ("left", "right", "back")) and not enclosure["front"]
        assert enclosure["back_direction"] == "+Y"
        inner, outer = shells[0][0], shells[1][0]
        z0 = enclosure["bottom_z_m"]
        ib = [(x, y, z0) for x, y, _ in inner]
        ob = [(x, y, z0) for x, y, _ in outer]

        def quad(a, b, c, d):
            s.tri(a, b, c, "hoodWalls")
            s.tri(a, c, d, "hoodWalls")

        for i in range(len(inner)):
            j = (i + 1) % len(inner)
            if i < len(inner) // 4:  # -Y front remains open, rim at hood height
                quad(inner[i], inner[j], outer[j], outer[i])
            else:  # +X side, +Y back, -X side extend to grill base
                quad(inner[i], inner[j], ib[j], ib[i])
                quad(outer[i], outer[j], ob[j], ob[i])
                quad(ib[i], ib[j], ob[j], ob[i])
        for i in (0, len(inner) // 4):
            quad(inner[i], outer[i], ob[i], ib[i])
    else:
        s.strip(shells[0][0], shells[1][0], "hoodWalls")
    s.strip(shells[0][-1], shells[1][-1], "hoodWalls")
    return s, top


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--coarse", action="store_true")
    args = parser.parse_args()
    p = json.loads((ROOT / "geometry/parameters.json").read_text())
    assert p["units"] == "m", "Geometry must use metres"
    g = p["grill"]
    assert g["front_holes"] == g["back_holes"] == len(g["hole_centers_x_assumed"]) == 4, (
        "Four holes per side required"
    )
    assert p["environment"]["domain_bounds_m"] == [-2, 2, -2, 2, 0, 3.46], (
        "Adapt generator for a different domain"
    )
    assert p["environment"]["wind_m_s"] == 0, "Wind boundary conditions are not implemented"
    assert (
        0
        < p["assumptions"]["wall_thickness_m"]
        < g["hole_center_z_assumed"] - g["hole_diameter"] / 2
    )
    assert (
        0
        < g["hole_center_z_assumed"] - g["hole_diameter"] / 2
        < g["hole_center_z_assumed"] + g["hole_diameter"] / 2
        < g["height"]
    )
    out = ROOT / "constant/triSurface"
    out.mkdir(parents=True, exist_ok=True)
    h, top = hood(p)
    from skewers import save as save_skewers

    save_skewers(p, out / "skewers.stl")
    if (ROOT / "constant/polyMesh/boundary").exists():
        (ROOT / "geometry/MESH_REBUILD_REQUIRED").write_text(
            "Geometry or boundary fields regenerated; run ./mangal mesh before solving.\n"
        )
    report = {
        "grill": grill(p).save(out / "grill.stl"),
        "hood": h.save(out / "hood.stl"),
        "outlet_z_m": top,
        "coarse": args.coarse,
    }
    report["grill_offset_x_m"] = g.get("offset_x_m", 0.0)
    report["enclosure"] = p["hood"].get("enclosure")
    report["Q_m3_s"] = fan_m3_s(p)
    report["outlet_velocity_m_s"] = report["Q_m3_s"] / (
        math.pi * p["duct"]["second_diameter"] ** 2 / 4
    )
    (ROOT / "geometry/generated.json").write_text(json.dumps(report, indent=2) + "\n")
    # The domain ceiling intersects the duct; only its internal disk becomes exhaust.
    n = 50 if args.coarse else 64
    nz = round(top / 4 * n)
    write(
        "system/blockMeshDict",
        f"""convertToMeters 1;
vertices ((-2 -2 0) (2 -2 0) (2 2 0) (-2 2 0) (-2 -2 {top}) (2 -2 {top}) (2 2 {top}) (-2 2 {top}));
blocks (hex (0 1 2 3 4 5 6 7) ({n} {n} {nz}) simpleGrading (1 1 1));
edges ();
boundary
(
 floor {{ type wall; faces ((0 3 2 1)); }}
 ambient {{ type patch; faces ((0 1 5 4) (1 2 6 5) (2 3 7 6) (3 0 4 7)); }}
 top {{ type patch; faces ((4 5 6 7)); }}
);
mergePatchPairs ();
""",
    )
    write(
        "system/topoSetDict",
        f"""actions
(
 {{ name exhaustFaces; type faceSet; action new; source patchToFace; sourceInfo {{ patches (top exhaust); }} }}
 {{ name exhaustFaces; type faceSet; action subset; source cylinderToFace;
    sourceInfo {{ point1 (0 0 {top - 1e-6}); point2 (0 0 {top + 1e-6}); radius {p["duct"]["second_diameter"] / 2}; }} }}
 {{ name ambientFaces; type faceSet; action new; source patchToFace; sourceInfo {{ patch ambient; }} }}
 {{ name ambientFaces; type faceSet; action add; source patchToFace; sourceInfo {{ patches (top exhaust); }} }}
 {{ name ambientFaces; type faceSet; action subtract; source faceToFace; sourceInfo {{ set exhaustFaces; }} }}
);
""",
    )
    write(
        "system/createPatchDict",
        """pointSync false;
patches
(
 { name exhaust; patchInfo { type patch; } constructFrom set; set exhaustFaces; }
 { name ambient; patchInfo { type patch; } constructFrom set; set ambientFaces; }
);
""",
    )
    hole_boxes = []
    hole_levels = []
    for side in (-1, 1):
        for i, x in enumerate(p["grill"]["hole_centers_x_assumed"]):
            x += g.get("offset_x_m", 0.0)
            y = g.get("offset_y_m", 0) + side * p["grill"]["depth"] / 2
            z = p["grill"]["hole_center_z_assumed"]
            name = f"hole_{side + 1}_{i}"
            hole_boxes.append(
                f"{name} {{ type searchableBox; min ({x - 0.014} {y - 0.012} {z - 0.014}); max ({x + 0.014} {y + 0.012} {z + 0.014}); }}"
            )
            hole_levels.append(f"{name} {{ mode inside; levels ((1e15 6)); }}")
    write(
        "system/snappyHexMeshDict",
        f""" castellatedMesh true;
snap true;
addLayers false;
geometry
{{
 grill.stl {{ type triSurfaceMesh; name grill; regions {{ grillWalls {{ name grillWalls; }} heater {{ name heater; }} }} }}
 skewers.stl {{ type triSurfaceMesh; name skewers; regions {{ meatWalls {{ name meatWalls; }} skewerWalls {{ name skewerWalls; }} }} }}
 hood.stl {{ type triSurfaceMesh; name hood; regions {{ hoodWalls {{ name hoodWalls; }} }} }}
 plume {{ type searchableBox; min ({g.get("offset_x_m", 0) - g["width"] / 2 - 0.075} {g.get("offset_y_m", 0) - g["depth"] / 2 - 0.075} 0.02); max ({g.get("offset_x_m", 0) + g["width"] / 2 + 0.075} {g.get("offset_y_m", 0) + g["depth"] / 2 + 0.075} 1.2); }}
 duct {{ type searchableBox; min (-0.18 -0.18 1.15); max (0.18 0.18 {top}); }}
 bed {{ type searchableBox; min ({g.get("offset_x_m", 0) - g["width"] / 2} {g.get("offset_y_m", 0) - g["depth"] / 2} 0.005); max ({g.get("offset_x_m", 0) + g["width"] / 2} {g.get("offset_y_m", 0) + g["depth"] / 2} 0.10); }}
 {" ".join(hole_boxes)}
}}
castellatedMeshControls
{{
 maxLocalCells 4000000; maxGlobalCells 8000000; minRefinementCells 0;
 nCellsBetweenLevels 3;
 features ();
 refinementSurfaces
 {{
   skewers {{ level (5 5); patchInfo {{ type wall; }} }}
   grill {{ level (4 4); patchInfo {{ type wall; }} }}
   hood {{ level (4 4); patchInfo {{ type wall; }} }}
 }}
 resolveFeatureAngle 30;
 refinementRegions
 {{ bed {{ mode inside; levels ((1e15 4)); }} plume {{ mode inside; levels ((1e15 2)); }} duct {{ mode inside; levels ((1e15 3)); }} {" ".join(hole_levels)} }}
 locationInMesh (1 1 0.5);
 allowFreeStandingZoneFaces true;
}}
snapControls
{{ nSmoothPatch 3; tolerance 1.5; nSolveIter 50; nRelaxIter 8; nFeatureSnapIter 15;
 implicitFeatureSnap true; explicitFeatureSnap false; multiRegionFeatureSnap true; }}
addLayersControls
{{ relativeSizes true; layers {{}} expansionRatio 1.2; finalLayerThickness 0.3; minThickness 0.1;
 nGrow 0; featureAngle 60; nRelaxIter 3; nSmoothSurfaceNormals 1; nSmoothNormals 3; nSmoothThickness 10;
 maxFaceThicknessRatio 0.5; maxThicknessToMedialRatio 0.3; minMedialAxisAngle 90;
 nBufferCellsNoExtrude 0; nLayerIter 50; }}
meshQualityControls
{{
 #includeEtc "caseDicts/meshQualityDict"
 nSmoothScale 4; errorReduction 0.75;
}}
mergeTolerance 1e-6;
""",
    )
    q = report["Q_m3_s"]
    temp = p["environment"]["air_temperature_K"]
    power = p["physics"]["heat_to_air_W"]
    wall = "type noSlip;"
    fields = {
        "U": (
            "volVectorField",
            "[0 1 -1 0 0 0 0]",
            "(0 0 0)",
            {
                "ambient": "type pressureInletOutletVelocity; value uniform (0 0 0);",
                "exhaust": f"type flowRateOutletVelocity; volumetricFlowRate constant {q:.14g}; value uniform (0 0 {report['outlet_velocity_m_s']:.10g});",
                "floor": wall,
                "grillWalls": wall,
                "heater": wall,
                "hoodWalls": wall,
            },
        ),
        "T": (
            "volScalarField",
            "[0 0 0 1 0 0 0]",
            str(temp),
            {
                "ambient": f"type inletOutlet; inletValue uniform {temp}; value uniform {temp};",
                "exhaust": "type zeroGradient;",
                "heater": f"type externalWallHeatFluxTemperature; mode power; Q constant {power}; kappaMethod fluidThermo; value uniform {temp};",
            },
        ),
        "p_rgh": (
            "volScalarField",
            "[1 -1 -2 0 0 0 0]",
            "101325",
            {
                "ambient": "type totalPressure; p0 uniform 101325; rho rho; psi none; value uniform 101325;",
                "exhaust": "type fixedFluxPressure; value uniform 101325;",
                "floor": "type fixedFluxPressure; value uniform 101325;",
                "grillWalls": "type fixedFluxPressure; value uniform 101325;",
                "heater": "type fixedFluxPressure; value uniform 101325;",
                "hoodWalls": "type fixedFluxPressure; value uniform 101325;",
            },
        ),
        "p": (
            "volScalarField",
            "[1 -1 -2 0 0 0 0]",
            "101325",
            dict.fromkeys(
                ["ambient", "exhaust", "floor", "grillWalls", "heater", "hoodWalls"],
                "type calculated; value uniform 101325;",
            ),
        ),
    }
    for name, value, walltype in [
        ("k", "0.01", "kqRWallFunction"),
        ("omega", "10", "omegaWallFunction"),
        ("nut", "0", "nutkWallFunction"),
        ("alphat", "0", "compressible::alphatWallFunction"),
    ]:
        dims = {
            "k": "[0 2 -2 0 0 0 0]",
            "omega": "[0 0 -1 0 0 0 0]",
            "nut": "[0 2 -1 0 0 0 0]",
            "alphat": "[1 -1 -1 0 0 0 0]",
        }[name]
        bc = {
            b: f"type {walltype}; value uniform {value};"
            for b in ["floor", "heater", "grillWalls", "hoodWalls"]
        }
        bc.update(
            {
                b: (
                    f"type inletOutlet; inletValue uniform {value}; value uniform {value};"
                    if name in ("k", "omega")
                    else f"type calculated; value uniform {value};"
                )
                for b in ["ambient", "exhaust"]
            }
        )
        fields[name] = ("volScalarField", dims, value, bc)
    for name, (cls, dims, initial, bc) in fields.items():
        for patch in ["meatWalls", "skewerWalls"]:
            bc[patch] = bc.get("grillWalls", "type zeroGradient;")
        for b in ["ambient", "exhaust", "floor", "grillWalls", "heater", "hoodWalls"]:
            bc.setdefault(b, "type zeroGradient;")
        write(
            "0/" + name,
            f"dimensions {dims};\ninternalField uniform {initial};\nboundaryField\n{{\n"
            + "".join(f" {b} {{ {v} }}\n" for b, v in bc.items())
            + "}\n",
            cls,
        )
    control = ROOT / "system/controlDict"
    if control.exists():
        import re

        text = control.read_text()
        points = " ".join(
            f"({x + g.get('offset_x_m', 0):.10g} {g.get('offset_y_m', 0) + side * (g['depth'] / 2 - p['assumptions']['wall_thickness_m'] / 2):.10g} {g['hole_center_z_assumed']:.10g})"
            for side in (-1, 1)
            for x in g["hole_centers_x_assumed"]
        )
        text = re.sub(r"probeLocations\s*\([^;]*\);", f"probeLocations ({points});", text)
        control.write_text(text)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
