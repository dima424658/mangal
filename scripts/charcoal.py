"""Parameterized porous charcoal layer and a deterministic visual lump mesh."""

import math
import random


def bed_bounds(p):
    g = p["grill"]
    c = p["charcoal"]
    t = p["assumptions"]["wall_thickness_m"]
    x = g.get("offset_x_m", 0)
    y = g.get("offset_y_m", 0)
    return (
        [x - g["width"] / 2 + t, y - g["depth"] / 2 + t, t],
        [x + g["width"] / 2 - t, y + g["depth"] / 2 - t, t + c["bed_thickness_m"]],
    )


def ergun(p):
    c = p["charcoal"]
    epsilon = c["void_fraction"]
    diam = c["particle_diameter_m"]
    assert 0 < epsilon < 1 and diam > 0
    return (
        150 * (1 - epsilon) ** 2 / (epsilon**3 * diam**2),
        3.5 * (1 - epsilon) / (epsilon**3 * diam),
    )


def lumps(p):
    low, high = bed_bounds(p)
    rng = random.Random(413)
    diam = p["charcoal"]["particle_diameter_m"]
    spacing = diam * 1.05
    nx = max(1, int((high[0] - low[0]) / spacing))
    ny = max(1, int((high[1] - low[1]) / spacing))
    nz = max(1, round((high[2] - low[2]) / diam))
    phi = (1 + math.sqrt(5)) / 2
    ico = [
        (-1, phi, 0),
        (1, phi, 0),
        (-1, -phi, 0),
        (1, -phi, 0),
        (0, -1, phi),
        (0, 1, phi),
        (0, -1, -phi),
        (0, 1, -phi),
        (phi, 0, -1),
        (phi, 0, 1),
        (-phi, 0, -1),
        (-phi, 0, 1),
    ]
    ico = [tuple(v / math.hypot(*pt) for v in pt) for pt in ico]
    faces = [
        (0, 11, 5),
        (0, 5, 1),
        (0, 1, 7),
        (0, 7, 10),
        (0, 10, 11),
        (1, 5, 9),
        (5, 11, 4),
        (11, 10, 2),
        (10, 7, 6),
        (7, 1, 8),
        (3, 9, 4),
        (3, 4, 2),
        (3, 2, 6),
        (3, 6, 8),
        (3, 8, 9),
        (4, 9, 5),
        (2, 4, 11),
        (6, 2, 10),
        (8, 6, 7),
        (9, 8, 1),
    ]
    result = []
    for k in range(nz):
        for j in range(ny):
            for i in range(nx):
                step = [(high[0] - low[0]) / nx, (high[1] - low[1]) / ny, (high[2] - low[2]) / nz]
                center = [
                    low[0] + (i + 0.5) * step[0],
                    low[1] + (j + 0.5) * step[1],
                    low[2] + (k + 0.5) * step[2],
                ]
                center[0] += rng.uniform(-0.08, 0.08) * step[0]
                center[1] += rng.uniform(-0.08, 0.08) * step[1]
                scales = [
                    min(diam * 0.48, step[0] * 0.43),
                    min(diam * 0.48, step[1] * 0.43),
                    step[2] * 0.43,
                ]
                angle = rng.uniform(0, 2 * math.pi)
                ca, sa = math.cos(angle), math.sin(angle)
                vertices = []
                for vx, vy, vz in ico:
                    rough = rng.uniform(0.82, 1.12)
                    x = rough * scales[0] * vx
                    y = rough * scales[1] * vy
                    z = rough * scales[2] * vz
                    pt = (center[0] + x * ca - y * sa, center[1] + x * sa + y * ca, center[2] + z)
                    # Keep all visual pieces within the modeled bed envelope.
                    pt = tuple(max(low[d] + 0.0001, min(high[d] - 0.0001, pt[d])) for d in range(3))
                    vertices.append(pt)
                result.append((vertices, faces))
    return result
