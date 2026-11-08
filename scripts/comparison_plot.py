"""Comparable vector images of the same computed X-Z section; no GUI required."""

import html
from pathlib import Path

from cold_obj import COLORS
from cold_viewer import read_vtp


def section_image(folder, title):
    folder = Path(folder)
    data = read_vtp(folder / "visualization/charcoal/sections/frontSection.vtp")
    points = data["points"]
    cells = data["polys"]
    temperature = data["temperature_C"]
    width = 640
    height = 650
    scale = 450
    x_min = -0.9
    z_max = 1.35
    polygons = []
    index = 0
    while index < len(cells):
        n = cells[index]
        indices = cells[index + 1 : index + 1 + n]
        index += n + 1
        # Crop to a common view around the grill and hood.
        coords = [(points[3 * i], points[3 * i + 2]) for i in indices]
        if all(x < x_min or x > 0.5 or z < 0 or z > z_max for x, z in coords):
            continue
        value = sum(temperature[i] for i in indices) / n
        color = COLORS[max(0, min(7, int((value - 20) / 130 * 8)))]
        rgb = ",".join(str(round(c * 255)) for c in color)
        xy = " ".join(
            f"{(x - x_min) * scale:.2f},{(z_max - z) * scale + 30:.2f}" for x, z in coords
        )
        polygons.append(f'<polygon points="{xy}" fill="rgb({rgb})"/>')
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}"><defs><clipPath id="section"><rect x="0" y="30" width="640" height="610"/></clipPath></defs><rect width="100%" height="100%" fill="#101822"/><g clip-path="url(#section)">'
        + "".join(polygons)
        + '</g><text x="12" y="20" fill="white" font-family="sans-serif" font-size="15">'
        + html.escape(title)
        + '</text><text x="12" y="645" fill="white" font-family="sans-serif" font-size="13">X–Z, Y=−0.05 m; gas temperature 20–150 °C; iteration 200</text></svg>'
    )
    path = folder / "front-temperature.svg"
    path.write_text(svg)
    return path
