import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import workflow


class WorkflowTests(unittest.TestCase):
    def test_full_resume_plan_keeps_mpi_and_order(self):
        args = workflow.parser().parse_args(
            ["all", "--resume", "--iterations", "300", "--parallel", "4"]
        )
        commands = workflow.plan(args)
        self.assertEqual(
            commands[0],
            [
                workflow.PYTHON,
                "scripts/run_case.py",
                "run",
                "--parallel",
                "4",
                "--resume",
                "--iterations",
                "300",
            ],
        )
        self.assertEqual(commands[1][1], "scripts/export_obj.py")
        self.assertEqual(commands[2][1], "scripts/audit_holes.py")
        self.assertEqual(
            commands[3],
            [workflow.PYTHON, "scripts/visualize_cold.py", "--parallel", "4", "--charcoal"],
        )
        self.assertFalse(any("source.zip" in str(c) for c in commands))

    def test_native_solver_completion_with_mpi_finalisation(self):
        from run_case import solver_finished

        self.assertTrue(solver_finished("Time = 30\nEnd\n\nFinalising parallel run\n"))
        self.assertTrue(solver_finished("Time = 30\nEnd\n"))
        self.assertFalse(solver_finished("Time = 30\nSolving for U"))
        self.assertFalse(solver_finished("FOAM FATAL\nEnd\n"))

    def test_invalid_options_rejected_before_start(self):
        for options in [
            ["all", "--resume", "--cold"],
            ["all", "--resume", "--rebuild-mesh"],
            ["run", "--iterations", "0"],
            ["mesh", "--parallel", "0"],
        ]:
            with self.subTest(options=options), self.assertRaises(ValueError):
                workflow.plan(workflow.parser().parse_args(options))

    def test_failure_prevents_following_stage(self):
        with patch.object(workflow.subprocess, "Popen") as popen:
            popen.return_value.wait.return_value = 1
            with self.assertRaises(RuntimeError):
                workflow.execute([["first"], ["second"]])
            self.assertEqual(popen.call_count, 1)

    def test_cold_full_pipeline_targets_cold_case(self):
        commands = workflow.plan(workflow.parser().parse_args(["all", "--cold", "--coarse"]))
        self.assertIn("--cold", commands[0])
        self.assertIn("--coarse", commands[0])
        self.assertEqual(commands[2][-1], "validation/isothermal")
        self.assertNotIn("--charcoal", commands[-1])
