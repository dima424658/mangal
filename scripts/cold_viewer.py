#!/usr/bin/env python3
"""Package native VTK samples into a vtk.js viewer and ParaView-friendly directory."""

import base64
import copy
import json
import math
import re
import shutil
import struct
import xml.etree.ElementTree as ET
from pathlib import Path

from generate import grill, hood
from model_parameters import fan_m3_h

ROOT = Path(__file__).resolve().parents[1]


def read_array(element):
    if element is None:
        return []
    if element.get("format") == "ascii":
        return [float(x) for x in element.text.split()]
    raw = base64.b64decode("".join(element.itertext()))
    fmt = {
        "Float32": "f",
        "Float64": "d",
        "Int32": "i",
        "Int64": "q",
        "UInt32": "I",
        "UInt64": "Q",
    }[element.get("type")]
    size = struct.unpack_from("<Q", raw)[0]
    assert len(raw) >= 8 + size
    return list(struct.unpack("<" + fmt * (size // struct.calcsize(fmt)), raw[8 : 8 + size]))


def read_vtp(path):
    tree = ET.parse(path)
    points = read_array(tree.find(".//Points/DataArray"))
    vectors = read_array(tree.find('.//PointData/DataArray[@Name="U"]'))
    assert len(points) % 3 == 0 and len(vectors) == len(points), (path, len(points), len(vectors))
    data = {
        "points": points,
        "speed": [math.hypot(*vectors[i : i + 3]) for i in range(0, len(vectors), 3)],
    }
    temperature = tree.find('.//PointData/DataArray[@Name="T"]')
    if temperature is not None:
        data["temperature_C"] = [v - 273.15 for v in read_array(temperature)]
        assert len(data["temperature_C"]) == len(data["speed"])
    for tag, key in [("Polys", "polys"), ("Lines", "lines")]:
        section = tree.find(".//" + tag)
        cells = []
        if section is not None:
            conn = read_array(section.find('DataArray[@Name="connectivity"]'))
            offsets = read_array(section.find('DataArray[@Name="offsets"]'))
            begin = 0
            for end in offsets:
                end = int(end)
                indices = conn[begin:end]
                assert all(0 <= i < len(points) // 3 for i in indices)
                cells += [len(indices), *indices]
                begin = end
            assert begin == len(conn)
        data[key] = cells
    assert all(math.isfinite(v) for v in points + data["speed"])
    return data


def solid(shape):
    regions, stats = shape.validated_regions()
    vertices = []
    polys = []
    index = {}
    for triangles in regions.values():
        for tri in triangles:
            face = []
            for pt in tri:
                if pt not in index:
                    index[pt] = len(vertices) // 3
                    vertices.extend(pt)
                face.append(index[pt])
            polys.extend([3, *face])
    return {"points": vertices, "polys": polys, "lines": [], "speed": []}


def add_skewers(data, p):
    from skewers import shapes

    for shape in shapes(p):
        other = solid(shape)
        offset = len(data["points"]) // 3
        data["points"].extend(other["points"])
        for i in range(0, len(other["polys"]), 4):
            data["polys"].extend([3, *[v + offset for v in other["polys"][i + 1 : i + 4]]])
    return data


def write_geometry_vtp(data, path):
    path.parent.mkdir(exist_ok=True)
    conn = []
    offsets = []
    i = 0
    while i < len(data["polys"]):
        n = data["polys"][i]
        conn.extend(data["polys"][i + 1 : i + 1 + n])
        offsets.append(len(conn))
        i += n + 1
    root = ET.Element("VTKFile", type="PolyData", version="0.1", byte_order="LittleEndian")
    pd = ET.SubElement(root, "PolyData")
    piece = ET.SubElement(
        pd, "Piece", NumberOfPoints=str(len(data["points"]) // 3), NumberOfPolys=str(len(offsets))
    )
    points = ET.SubElement(piece, "Points")
    arr = ET.SubElement(points, "DataArray", type="Float64", NumberOfComponents="3", format="ascii")
    arr.text = " ".join(f"{v:.10g}" for v in data["points"])
    polys = ET.SubElement(piece, "Polys")
    for name, values in [("connectivity", conn), ("offsets", offsets)]:
        arr = ET.SubElement(polys, "DataArray", type="Int32", Name=name, format="ascii")
        arr.text = " ".join(map(str, values))
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def write_bundle(data, out):
    root = ET.Element(
        "VTKFile", type="vtkMultiBlockDataSet", version="1.0", byte_order="LittleEndian"
    )
    blocks = ET.SubElement(root, "vtkMultiBlockDataSet")
    groups = [
        ("Geometry", [("Grill", "geometry/grill.vtp"), ("Hood", "geometry/hood.vtp")]),
        (
            "Sections",
            [
                ("Front", "sections/frontSection.vtp"),
                ("Side", "sections/sideSection.vtp"),
                ("Horizontal", "sections/horizontalSection.vtp"),
            ],
        ),
        (
            "Streamlines",
            [
                ("Intake", "streamlines/intakeStreamlines.vtp"),
                ("Over_grill", "streamlines/grillStreamlines.vtp"),
            ],
        ),
    ]
    for index, (name, files) in enumerate(groups):
        block = ET.SubElement(blocks, "Block", index=str(index), name=name)
        for i, (label, file) in enumerate(files):
            ET.SubElement(block, "DataSet", index=str(i), name=label, file=file)
    for name in ["grill", "hood"]:
        write_geometry_vtp(data[name], out / f"geometry/{name}.vtp")
    ET.ElementTree(root).write(out / "cold-view.vtm", encoding="utf-8", xml_declaration=True)


def main():
    out = ROOT / "visualization/cold"
    out.mkdir(parents=True, exist_ok=True)
    case = ROOT / "validation/isothermal"
    params = json.loads((case / "geometry/parameters.json").read_text())
    times = [p for p in case.iterdir() if p.is_dir() and p.name.isdigit() and p.name != "0"]
    latest = max(times, key=lambda p: float(p.name)).name
    data = {}
    for filename, key in [
        ("frontSection", "front"),
        ("sideSection", "side"),
        ("horizontalSection", "horizontal"),
    ]:
        src = case / f"postProcessing/coldSections/{latest}/{filename}.vtp"
        dest = out / "sections" / src.name
        dest.parent.mkdir(exist_ok=True)
        shutil.copy2(src, dest)
        data[key] = read_vtp(dest)
    for name, key in [("intakeStreamlines", "intake"), ("grillStreamlines", "grillFlow")]:
        src = case / f"postProcessing/sets/{name}/{latest}/track0.vtp"
        dest = out / "streamlines" / f"{name}.vtp"
        dest.parent.mkdir(exist_ok=True)
        shutil.copy2(src, dest)
        data[key] = read_vtp(dest)
    physical = copy.deepcopy(params)
    physical["assumptions"]["duct_mesh_extension_m"] = 0
    data["grill"] = add_skewers(solid(grill(physical)), physical)
    data["hood"] = solid(hood(physical)[0])
    write_bundle(data, out)
    max_speed = max(max(d["speed"]) for d in data.values() if d["speed"])
    metadata = {
        "fan_m3_h": fan_m3_h(params),
        "iteration": int(latest),
        "heat_W": 0,
        "max_sampled_speed_m_s": max_speed,
        "status": "preliminary isothermal RANS; strict SIMPLE convergence not reached",
        "datasets": {
            key: {
                "points": len(d["points"]) // 3,
                "polygons": bool(d["polys"]),
                "lines": bool(d["lines"]),
            }
            for key, d in data.items()
        },
    }
    speeds = re.findall(
        r"max\(mag\(U\)\) = ([\d.eE+-]+)", (case / "logs/simpleFoam.log").read_text()
    )
    metadata["max_volume_speed_m_s"] = float(speeds[-1]) if speeds else max_speed
    (out / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    # Keep the original VTK files at full precision. Only the browser payload is rounded.
    for d in data.values():
        d["points"] = [float(f"{v:.7g}") for v in d["points"]]
        d["speed"] = [float(f"{v:.6g}") for v in d["speed"]]
    html = (ROOT / "scripts/cold_viewer_template.html").read_text()
    html = html.replace("__FLOW_M3_H__", f"{fan_m3_h(params):.3f}").replace("__ITERATION__", latest)
    html = html.replace(
        "__MAX_SPEED__", f"{metadata['max_volume_speed_m_s']:.2f}".replace(".", ",")
    )
    html = html.replace("__DATA_JSON__", json.dumps(data, separators=(",", ":"), allow_nan=False))
    (out / "cold-flow.html").write_text(html)
    print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    main()
