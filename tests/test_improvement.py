from capo.process import CleanupUncertain
import argparse
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from capo.improvement import add_improvement, verify_baseline, verify_governance_changes
from capo.providers import WorkerError, run_process
from capo.repository import git
from capo.store import Store
from capo.runtime import Runtime
from test_capo import FakeProviders


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

    def test_planner_clarification_waits_then_owner_answer_resumes(self):
        objective = self.queue()
        class ClarifyingProviders(FakeProviders):
            asked = False
            def call(self, provider, prompt, schema, cwd, directory):
                if "planner for" in prompt and not self.asked:
                    self.asked = True
                    self.calls.append((provider, prompt))
                    return {"summary": "Should addition support negative inputs?", "tasks": [], "acceptance": []}
                return super().call(provider, prompt, schema, cwd, directory)
        providers = ClarifyingProviders()
        waiting = Runtime(self.store, providers).run(objective["id"])
        self.assertEqual(waiting["status"], "awaiting_input")
        self.assertEqual(waiting["question"], "Should addition support negative inputs?")
        self.assertEqual(waiting["calls"], 1)
        self.assertNotIn("accepted_tree", waiting)
        again = Runtime(self.store, providers).run(objective["id"])
        self.assertEqual(again["calls"], 1)
        self.store.add_followup(objective["id"], "answer-one", "Yes, preserve negative addition")
        self.assertEqual(self.store.get(objective["id"])["status"], "queued")
        completed = Runtime(self.store, providers).run(objective["id"])
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["workspace"], waiting["workspace"])
        self.assertNotIn("question", completed)
        self.assertIn("Yes, preserve negative addition", providers.calls[1][1])
        self.assertGreater(completed["calls"], waiting["calls"])

    def test_followup_during_acceptance_forces_replan(self):
        objective = self.queue()
        store = self.store
        class FollowupProvider(FakeProviders):
            added = False
            plans = 0
            def call(self, provider, prompt, schema, cwd, directory):
                if "planner for" in prompt:
                    self.plans += 1
                result = super().call(provider, prompt, schema, cwd, directory)
                if "acceptance for" in prompt and not self.added:
                    self.added = True
                    store.add_followup(objective["id"], "event-one", "Also preserve negative addition")
                return result
        providers = FollowupProvider()
        result = Runtime(store, providers).run(objective["id"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(providers.plans, 2)
        self.assertEqual(len(result["followups"]), 1)
        self.assertEqual(result["workspace"], objective["workspace"])

    def test_followups_are_deduplicated_without_overwriting_active_state(self):
        objective = self.queue()
        objective["status"] = "running"
        self.store.save(objective, "fixture_running")
        for _ in range(2):
            self.store.add_followup(objective["id"], "event-one", "More context")
        self.assertEqual(len(self.store.followups(objective["id"])), 1)
        self.assertEqual(self.store.get(objective["id"])["status"], "running")

    def test_followup_preserves_completed_candidate_and_budget(self):
        objective = self.queue()
        completed = Runtime(self.store, FakeProviders()).run(objective["id"])
        updated = self.store.add_followup(objective["id"], "event-later", "Check another case")
        self.assertEqual(updated["status"], "queued")
        self.assertEqual(updated["calls"], completed["calls"])
        self.assertEqual(updated["round"], completed["round"])
        self.assertEqual(updated["workspace"], completed["workspace"])
        self.assertNotIn("accepted_tree", updated)
        resumed = Runtime(self.store, FakeProviders()).run(objective["id"])
        self.assertEqual(resumed["status"], "completed")
        self.assertGreater(resumed["calls"], completed["calls"])
        self.assertEqual(len(resumed["followups"]), 1)

    def test_retry_refuses_uncertain_remote_attempt(self):
        objective = self.queue()
        directory = self.store.home / "artifacts" / objective["id"] / "old-attempt"
        directory.mkdir(parents=True)
        (directory / "remote.json").write_text('{}')
        with patch("capo.transport.reconcile", side_effect=ValueError("guest still active")):
            with self.assertRaisesRegex(ValueError, "still active"):
                Runtime(self.store, FakeProviders()).run(objective["id"], retry=True)
        self.assertEqual(self.store.get(objective["id"])["calls"], 0)
        self.assertFalse((directory / "remote-exit.json").exists())

    def test_core_governance_cannot_be_changed_by_autonomous_improvement(self):
        objective = self.queue()
        for path in ("capo/runtime.py", "capo/improvement.py", "capo/process.py", "capo",
                     "capo/cli.py", "capo/contracts.py", "CAPO/Runtime.py", "capo/__init__.py",
                     "capo/__main__.py", "pyproject.toml", "setup.py", "setup.cfg",
                     "sitecustomize.py", "usercustomize.py", "capo/runtime/__init__.py",
                     "sitecustomize/__init__.py", "capo/social_tools.py", "capo/social_tools/__init__.py"):
            for delete in (False, True):
                with self.subTest(path=path, delete=delete), self.assertRaisesRegex(ValueError, "owner review"):
                    verify_governance_changes(objective, [{"path": path, "content": "", "delete": delete}])
        verify_governance_changes(objective, [{"path": "capo/new_feature.py", "content": "", "delete": False}])
        verify_governance_changes(objective, [{"path": "tests/test_feature.py", "content": "", "delete": False}])

    def test_ordinary_objective_cannot_bypass_capo_governance(self):
        objective = self.queue()
        objective["kind"] = "development"
        objective["workspace"] = str(self.repo)
        with self.assertRaisesRegex(ValueError, "owner review"):
            verify_governance_changes(objective, [{"path": "capo/runtime.py", "content": "", "delete": True}])

    def test_followup_cannot_bypass_interrupted_running_recovery(self):
        objective = self.queue()
        objective["status"] = "running"
        self.store.save(objective, "interrupted_fixture")
        self.store.add_followup(objective["id"], "event-new", "Continue")
        with self.assertRaisesRegex(ValueError, "recover first"):
            Runtime(self.store, FakeProviders()).run(objective["id"])
        self.assertEqual(self.store.get(objective["id"])["status"], "running")

    def test_followup_cannot_bypass_live_process_reconciliation(self):
        import json
        import os
        objective = self.queue()
        objective["status"] = "blocked"
        self.store.save(objective, "blocked_fixture")
        directory = self.store.home / "artifacts" / objective["id"] / "old-attempt"
        directory.mkdir(parents=True)
        (directory / "process.json").write_text(json.dumps({"pid": os.getpid()}))
        self.store.add_followup(objective["id"], "event-new", "Continue")
        with self.assertRaisesRegex(CleanupUncertain, "still be active"):
            Runtime(self.store, FakeProviders()).run(objective["id"])
        self.assertEqual(self.store.get(objective["id"])["calls"], 0)

    def test_correct_candidate_passes_original_regressions(self):
        objective = self.queue()
        run_process(objective["checks"][0], self.repo, self.root / "check", 10)
        verify_baseline(objective)


if __name__ == "__main__":
    unittest.main()
