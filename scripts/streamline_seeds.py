"""Deterministic, grouped visualization seeds derived from model geometry."""

import math


def groups(p):
    g = p["grill"]
    ox = g.get("offset_x_m", 0)
    oy = g.get("offset_y_m", 0)
    holes = []
    radius = g["hole_diameter"] * 0.25
    t = p["assumptions"]["wall_thickness_m"]
    for side in [-1, 1]:
        y = oy + side * (g["depth"] / 2 - t / 2)
        for x in g["hole_centers_x_assumed"]:
            holes.append((ox + x, y, g["hole_center_z_assumed"]))
            for k in range(6):
                angle = 2 * math.pi * k / 6
                holes.append(
                    (
                        ox + x + radius * math.cos(angle),
                        y,
                        g["hole_center_z_assumed"] + radius * math.sin(angle),
                    )
                )
    grill = [
        (
            ox - g["width"] / 2 + 0.035 + i * (g["width"] - 0.07) / 9,
            oy - g["depth"] / 2 + 0.035 + j * (g["depth"] - 0.07) / 7,
            0.11,
        )
        for i in range(10)
        for j in range(8)
    ]
    intake = [
        (
            ox - g["width"] / 2 + 0.025 + i * (g["width"] - 0.05) / 11,
            -p["hood"]["inlet_depth"] / 2 - 0.10,
            0.21 + j * (p["hood"]["bottom_z"] - 0.21 - 0.025) / 5,
        )
        for i in range(12)
        for j in range(6)
    ]
    return {"intakeStreamlines": intake, "grillStreamlines": grill, "holeStreamlines": holes}


def update(path, p):
    text = path.read_text()
    prefix = text[: text.index(" intakeStreamlines")]
    definitions = []
    for name, points in groups(p).items():
        seeds = "\n".join("    (" + " ".join(f"{v:.10g}" for v in point) + ")" for point in points)
        definitions.append(f""" {name}
 {{
  type streamLine; libs (fieldFunctionObjects);
  fields (U T); U U; setFormat vtk; direction forward;
  lifeTime 3500; trackLength 0.008; interpolationScheme cellPoint;
  seedSampleSet {{ type cloud; axis xyz; points (\n{seeds}\n  ); }}
 }}""")
    path.write_text(prefix + "\n".join(definitions) + "\n}\n")
