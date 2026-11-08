import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from comparison import parameters
from generate import grill, hood
from model_parameters import canonical_parameters, fan_m3_h, fan_m3_s, mesh_signature, outlet_height


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.base = json.loads(
            (Path(__file__).resolve().parents[1] / "geometry/parameters.json").read_text()
        )

    def test_flow_conversion_and_legacy_reading(self):
        self.assertAlmostEqual(fan_m3_s(parameters(self.base, 0.5, 350)), 350 / 3600)
        self.assertAlmostEqual(fan_m3_s(parameters(self.base, 0.5, 600)), 1 / 6)
        old = json.loads(json.dumps(self.base))
        old["physics"].pop("exhaust_m3_h")
        old["physics"]["exhaust_cfm"] = 206.0022225420168
        old["fan"].pop("flow_m3_h")
        old["fan"]["flow_cfm"] = 206.0022225420168
        self.assertAlmostEqual(fan_m3_h(old), 350)
        self.assertEqual(canonical_parameters(old), canonical_parameters(self.base))

    def test_lowering_preserves_hood_shape_and_duct_lengths(self):
        p = parameters(self.base, 0.35, 600)
        self.assertAlmostEqual(p["hood"]["bottom_z"], 0.51)
        self.assertAlmostEqual(p["hood"]["top_z"], 1.01)
        self.assertAlmostEqual(outlet_height(p), 3.31)
        self.assertAlmostEqual(hood(p)[1], p["environment"]["domain_bounds_m"][5])
        self.assertEqual(p["duct"], self.base["duct"])
        self.assertEqual(p["grill"], self.base["grill"])
        self.assertAlmostEqual(self.base["hood"]["bottom_z"], 0.66)
        self.assertEqual(
            grill(p).validated_regions()[1]["triangles"],
            grill(self.base).validated_regions()[1]["triangles"],
        )

    def test_only_flow_change_reuses_mesh(self):
        self.assertEqual(mesh_signature(parameters(self.base, 0.5, 600)), mesh_signature(self.base))
        self.assertNotEqual(
            mesh_signature(parameters(self.base, 0.35, 350)), mesh_signature(self.base)
        )
