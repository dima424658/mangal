import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from comparison_viewer import build


class ComparisonViewerTests(unittest.TestCase):
    def test_ready_and_pending_variants_share_one_page(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / "ready/visualization/charcoal"
            folder.mkdir(parents=True)
            (root / "ready/COMPLETED.json").write_text("{}")
            (folder / "metadata.json").write_text(
                json.dumps({"iteration": 200, "summary": {"iteration": 200, "outlet_mean_T_C": 42}})
            )
            (folder / "charcoal-flow.html").write_text(
                '<script>const datasets={"front":{"points":[]}};</script>'
            )
            result = build(root, [("ready", 0.5, 600), ("pending", 0.35, 350)])
            text = result.read_text()
            data = json.loads(
                re.search(
                    r'<script type="application/json" id="comparison-data">(.*?)</script>',
                    text,
                    re.S,
                )[1]
            )
            self.assertEqual(data[0]["iteration"], 200)
            self.assertEqual(data[0]["label"], "50 см / 600 м³/ч")
            self.assertIsNone(data[1]["data"])
            self.assertNotIn("<iframe", text)
            self.assertIn("renderer.setActiveCamera(camera)", text)

    def test_mismatched_snapshot_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / "case/visualization/charcoal"
            folder.mkdir(parents=True)
            (root / "case/COMPLETED.json").write_text("{}")
            (folder / "metadata.json").write_text(
                json.dumps({"iteration": 200, "summary": {"iteration": 209}})
            )
            (folder / "charcoal-flow.html").write_text("<script>const datasets={};</script>")
            with self.assertRaises(ValueError):
                build(root, [("case", 0.5, 350)])
