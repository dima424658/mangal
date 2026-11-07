"""Canonical fan flow in CFM; OpenFOAM conversion uses exact international foot."""

CFM_TO_M3_S = 0.028316846592 / 60


def fan_cfm(p):
    physics = p["physics"]
    if "exhaust_cfm" in physics:
        return float(physics["exhaust_cfm"])
    # Read archived cases created before the switch to CFM.
    return float(physics["exhaust_m3_h"]) / 3600 / CFM_TO_M3_S


def fan_m3_s(p):
    return fan_cfm(p) * CFM_TO_M3_S


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
