"""Ten closed skewers with six rounded meat pieces each; metres."""

import math

from generate import Surface


def shapes(p):
    cfg = p.get("skewers", {})
    if not cfg.get("enabled"):
        return []
    g = p["grill"]
    ox = g.get("offset_x_m", 0)
    oy = g.get("offset_y_m", 0)
    meat_radius = cfg["meat_piece_diameter_m"] / 2
    result = []
    for i in range(cfg["count"]):
        x = ox - g["width"] / 2 + 0.04 + i * (g["width"] - 0.08) / (cfg["count"] - 1)
        z = g["height"] + cfg["rod_diameter_m"] / 2
        centres = [
            oy - g["depth"] / 2 + 0.045 + j * (g["depth"] - 0.09) / (cfg["pieces_per_skewer"] - 1)
            for j in range(cfg["pieces_per_skewer"])
        ]
        # Profile is the union of a rod and rounded meat bodies: no internal intersecting faces.
        stations = [(oy - g["depth"] / 2 - 0.10, cfg["rod_diameter_m"] / 2, "skewerWalls")]
        for c in centres:
            for k in range(9):
                a = -math.pi / 2 + k * math.pi / 8
                stations.append(
                    (
                        c + meat_radius * math.sin(a),
                        max(cfg["rod_diameter_m"] / 2, meat_radius * math.cos(a)),
                        "meatWalls",
                    )
                )
        stations.append((oy + g["depth"] / 2 + 0.03, cfg["rod_diameter_m"] / 2, "skewerWalls"))
        s = Surface()
        rings = []
        for y, r, region in stations:
            rings.append(
                [
                    (
                        x + r * math.cos(2 * math.pi * k / 24),
                        y,
                        z + r * math.sin(2 * math.pi * k / 24),
                    )
                    for k in range(24)
                ]
            )
        s.fan(rings[0], "skewerWalls")
        s.fan(rings[-1], "skewerWalls")
        for j in range(len(rings) - 1):
            region = (
                "meatWalls"
                if stations[j][2] == stations[j + 1][2] == "meatWalls"
                and stations[j + 1][0] - stations[j][0] < 0.02
                else "skewerWalls"
            )
            s.strip(rings[j], rings[j + 1], region)
        result.append(s)
    return result


def save(p, path):
    # Surface.save validates one connected component; validate each skewer separately.
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        texts = []
        for i, s in enumerate(shapes(p)):
            f = __import__("pathlib").Path(tmp) / f"{i}.stl"
            s.save(f)
            texts.append(f.read_text())
        # OpenFOAM merges repeated named solid regions.
        path.write_text("".join(texts))
