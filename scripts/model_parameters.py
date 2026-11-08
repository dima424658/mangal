"""Canonical fan flow in m³/h, with legacy CFM input support."""

import copy

CFM_TO_M3_S = 0.028316846592 / 60
M3_H_TO_M3_S = 1 / 3600


def fan_m3_h(p):
    physics = p["physics"]
    if "exhaust_m3_h" in physics:
        return float(physics["exhaust_m3_h"])
    return float(physics["exhaust_cfm"]) * CFM_TO_M3_S * 3600


def fan_m3_s(p):
    return fan_m3_h(p) / 3600


def fan_cfm(p):
    return fan_m3_s(p) / CFM_TO_M3_S


def canonical_parameters(p):
    result = copy.deepcopy(p)
    flow = round(fan_m3_h(p), 9)
    result["physics"].pop("exhaust_cfm", None)
    result["physics"]["exhaust_m3_h"] = flow
    result.setdefault("fan", {}).pop("flow_cfm", None)
    result["fan"]["flow_m3_h"] = flow
    return result


def outlet_height(p):
    return (
        p["hood"]["top_z"]
        + p["assumptions"]["square_to_round_length_m"]
        + p["duct"]["first_length"]
        + p["assumptions"]["reducer_length_m"]
        + p["duct"]["second_length"]
    )


def mesh_signature(p):
    return {
        "skewers": p.get("skewers", {}),
        "mesh": p.get("mesh", {}),
        "units": p["units"],
        "grill": p["grill"],
        "hood": p["hood"],
        "duct": p["duct"],
        "bounds": p["environment"]["domain_bounds_m"],
        "solid_assumptions": {
            key: p["assumptions"][key]
            for key in ["wall_thickness_m", "square_to_round_length_m", "reducer_length_m"]
        },
    }


def mesh_revision(root):
    """Track topology changes without hashing every large mesh file at each launch."""
    result = {}
    for name in ["points", "faces", "owner", "neighbour", "boundary"]:
        stat = (root / "constant/polyMesh" / name).stat()
        result[name] = {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
    return result
