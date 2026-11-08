"""MPI orchestration checks independent of an available MPI runtime."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import parallel
from normalize_mesh import normalize_file


class ParallelTests(unittest.TestCase):
    def test_process_count(self):
        self.assertEqual(parallel.ranks("1"), 1)
        self.assertGreaterEqual(parallel.ranks("nproc"), 1)
        with self.assertRaises(ValueError):
            parallel.ranks("0")

    def test_processor_coordinate_normalization(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "points"
            backup = Path(directory) / "backup.gz"
            path.write_text("1\n(\n(1e-311 -2.0E-305 0.00125)\n)\n")
            result = normalize_file(path, backup)
            self.assertEqual(result["normalized_components"], 2)
            self.assertIn("(0 0 0.00125)", path.read_text())
            self.assertTrue(backup.exists())

    def test_subnormal_fields_preserve_physical_values(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "40").mkdir()
            field = root / "40/phi"
            field.write_text("FoamFile { format ascii; }\n3\n(\n-5.56e-309\n1.25e-6\n-2.5\n)\n")
            parallel.normalize_fields(root, "40")
            self.assertIn("0\n1.25e-6\n-2.5", field.read_text())
            self.assertTrue((root / "validation/field-io/40/phi.gz").exists())

    def test_newer_distributed_checkpoint_is_recovered_before_reuse(self):
        import json

        with tempfile.TemporaryDirectory() as directory:
            case = Path(directory)
            (case / "120").mkdir()
            mesh = case / "constant/polyMesh"
            mesh.mkdir(parents=True)
            for name in ["points", "faces", "owner", "neighbour", "boundary"]:
                (mesh / name).touch()
            for rank in range(2):
                base = case / f"processor{rank}"
                (base / "constant/polyMesh").mkdir(parents=True)
                (base / "200").mkdir()
                for name in ["U", "p_rgh", "T", "k", "omega", "phi"]:
                    (base / "200" / name).touch()
            parallel.remember(case, 2, "latest", True)
            calls = []

            def recover(stage, path, selection):
                self.assertEqual(selection, "200")
                (path / "200").mkdir()

            with (
                patch.object(parallel, "normalize_fields") as normalize,
                patch.object(parallel, "reconstruct", side_effect=recover) as reconstruct,
            ):
                parallel.decompose(lambda *a, **k: calls.append(a), case, 2)
            reconstruct.assert_called_once()
            self.assertEqual(calls, [])  # no destructive decomposePar -force
            self.assertTrue((case / "processor0/200/phi").exists())
            self.assertEqual(json.loads((case / ".decomposition.json").read_text())["time"], "200")
            self.assertTrue(normalize.called)

    def test_initial_fields_are_explicitly_included_in_reconstruction(self):
        calls = []
        parallel.reconstruct(lambda *a, **k: calls.append(a), Path("/private/tmp"), "0", ["Cz"])
        self.assertIn("-withZero", calls[0][1])
        self.assertIn("0", calls[0][1])

    def test_serial_does_not_decompose(self):
        calls = []
        with patch.dict(os.environ, {"MANGAL_PARALLEL": "1"}):
            parallel.tool(
                lambda *a, **k: calls.append((a, k)),
                "solve",
                "simpleFoam",
                [],
                Path("/private/tmp"),
                Path("/private/tmp/log"),
                kind="solve",
            )
        self.assertEqual(len(calls), 1)
        self.assertNotIn("-parallel", calls[0][0][1])

    def test_solver_is_reconstructed(self):
        with tempfile.TemporaryDirectory() as directory:
            case = Path(directory)
            calls = []
            with (
                patch.dict(os.environ, {"MANGAL_PARALLEL": "8"}),
                patch.object(parallel, "preflight"),
                patch.object(parallel, "decompose") as decompose,
                patch.object(
                    parallel,
                    "launcher",
                    side_effect=lambda command, count: ["mpirun", "-np", str(count), *command],
                ),
                patch.object(parallel, "reconstruct") as reconstruct,
                patch.object(parallel, "remember"),
            ):
                parallel.tool(
                    lambda *a, **k: calls.append((a, k)),
                    "solve",
                    "simpleFoam",
                    [],
                    case,
                    case / "log",
                    kind="solve",
                    selection="0",
                )
                decompose.assert_called_once()
                self.assertIn("-parallel", calls[0][0][1])
                self.assertEqual(calls[0][0][1][2], "8")
                reconstruct.assert_called_once_with(unittest.mock.ANY, case.resolve(), "latest")
                self.assertEqual(calls[0][1]["env"]["OMP_NUM_THREADS"], "1")


if __name__ == "__main__":
    unittest.main()
