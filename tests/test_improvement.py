import argparse
import os
import sys
import tempfile
import unittest
from pathlib import Path

from capo.improvement import add_improvement, verify_baseline
from capo.providers import WorkerError, run_process
from capo.repository import git
from capo.store import Store


class ImprovementCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        (self.repo / "capo").mkdir(parents=True)
        (self.repo / "capo/runtime.py").write_text("# Fixture Capo checkout\n")
        (self.repo / "tests").mkdir()
        (self.repo / "maths.py").write_text("def add(a,b):\n    return a+b\n")
        (self.repo / "tests/test_original.py").write_text(
            "import unittest\nfrom maths import add\nclass Regression(unittest.TestCase):\n"
            "    def test_add(self):\n        self.assertEqual(add(2,3), 5)\n")
        git(self.repo, "init", "-b", "main")
        git(self.repo, "config", "user.name", "Test")
        git(self.repo, "config", "user.email", "test@example.invalid")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "fixture")
        self.store = Store(self.root / "state")

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def queue(self):
        return add_improvement(self.store, argparse.Namespace(repo=self.repo, request="Improve reliability"))

    def test_regression_suite_is_preserved_outside_candidate(self):
        objective = self.queue()
        baseline = Path(objective["regression_baseline"]["path"])
        self.assertFalse(baseline.is_relative_to(Path(objective["workspace"])))
        verify_baseline(objective)
        # Delete the candidate tests and break the function. The frozen tests still fail.
        (self.repo / "tests/test_original.py").unlink()
        (self.repo / "maths.py").write_text("def add(a,b):\n    return a-b\n")
        with self.assertRaises(WorkerError):
            run_process(objective["checks"][0], self.repo, self.root / "check", 10)

    def test_baseline_modification_is_detected(self):
        objective = self.queue()
        baseline = Path(objective["regression_baseline"]["path"])
        (baseline / "tests/test_original.py").write_text("# weakened\n")
        with self.assertRaisesRegex(ValueError, "baseline changed"):
            verify_baseline(objective)

    def test_missing_baseline_is_not_an_ordinary_objective(self):
        with self.assertRaisesRegex(ValueError, "requires a frozen"):
            verify_baseline({"kind": "self_improvement"})

    def test_correct_candidate_passes_original_regressions(self):
        objective = self.queue()
        run_process(objective["checks"][0], self.repo, self.root / "check", 10)
        verify_baseline(objective)


if __name__ == "__main__":
    unittest.main()
