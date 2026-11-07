import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import charcoal_report


class MonitoringTests(unittest.TestCase):
    def test_saved_snapshot_does_not_use_later_monitoring(self):
        with tempfile.TemporaryDirectory() as directory:
            case = Path(directory)
            for folder, content in [
                ("120", "# header\n200 327.64\n"),
                ("200", "# header\n201 328\n209 329\n"),
            ]:
                p = case / "postProcessing/outletState" / folder / "values.dat"
                p.parent.mkdir(parents=True)
                p.write_text(content)
            with patch.object(charcoal_report, "CASE", case):
                self.assertEqual(charcoal_report.row("outletState", 200), [200, 327.64])
                with self.assertRaises(RuntimeError):
                    charcoal_report.row("outletState", 199)
