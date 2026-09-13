import argparse
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from capo.cli import add_objective, recover
from capo.contracts import PLAN, validate
from capo.providers import Providers, WorkerError, run_process
from capo.repository import apply_changes, git, safe_path, snapshot
from capo.runtime import Runtime, exclusive
from capo.store import Store


class FakeProviders:
    def __init__(self, reject_first=False, approve=True, malformed=False):
        self.calls = []
        self.reject_first = reject_first
        self.approve = approve
        self.malformed = malformed
        self.reviews = 0

    def call(self, provider, prompt, schema, cwd, directory):
        self.calls.append((provider, prompt))
        if "planner" in prompt:
            if self.malformed:
                return {"summary": "bad"}
            return {"summary": "Fix add", "acceptance": ["add(2, 3) returns 5"],
                    "tasks": [{"title": "Fix add", "instructions": "Use addition", "worker": "codex"}]}
        if "implementer" in prompt:
            return {"summary": "Fixed", "changes": [{"path": "maths.py", "delete": False,
                                                     "content": "def add(a, b):\n    return a + b\n"}]}
        if "reviewer" in prompt:
            self.reviews += 1
            approved = self.approve and not (self.reject_first and self.reviews == 1)
            return {"approved": approved, "findings": [] if approved else ["Needs another look"]}
        return {"accepted": True, "reason": "Acceptance evidence supports completion"}


class RepositoryCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-b", "main")
        git(self.repo, "config", "user.name", "Capo Test")
        git(self.repo, "config", "user.email", "test@example.invalid")
        (self.repo / "maths.py").write_text("def add(a, b):\n    return a - b\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "fixture")
        self.store = Store(self.root / "state")

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def objective(self, check=None, **options):
        args = argparse.Namespace(repo=self.repo, check=[check or
            f'{sys.executable} -c "from maths import add; assert add(2,3) == 5"'],
            max_calls=options.get("max_calls", 24), max_rounds=options.get("max_rounds", 3), timeout=10)
        return add_objective(self.store, args, "Fix addition")

    def test_full_cycle_and_patch_replay(self):
        objective = self.objective()
        fake = FakeProviders()
        result = Runtime(self.store, fake).run(objective["id"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual([provider for provider, _ in fake.calls], ["claude", "codex", "grok", "claude"])
        self.assertIn("a - b", (self.repo / "maths.py").read_text())
        patch_file = self.store.home / "artifacts" / objective["id"] / "changes.patch"
        git(self.repo, "apply", "--check", str(patch_file))
        self.assertEqual(git(Path(result["workspace"]), "remote"), "")
        Runtime(self.store, fake).run(objective["id"])
        self.assertEqual(len(fake.calls), 4)

    def test_review_rejection_revises_before_completion(self):
        objective = self.objective()
        fake = FakeProviders(reject_first=True)
        result = Runtime(self.store, fake).run(objective["id"])
        self.assertEqual(result["round"], 2)
        self.assertIn("Needs another look", fake.calls[4][1])

    def test_failed_check_cannot_be_overruled_by_models(self):
        objective = self.objective(check=f'{sys.executable} -c "raise SystemExit(1)"', max_rounds=1)
        with self.assertRaisesRegex(ValueError, "Revision limit"):
            Runtime(self.store, FakeProviders()).run(objective["id"])
        self.assertEqual(self.store.get(objective["id"])["status"], "blocked")

    def test_reviewer_cannot_be_overruled_by_ceo(self):
        objective = self.objective(max_rounds=1)
        with self.assertRaisesRegex(ValueError, "Revision limit"):
            Runtime(self.store, FakeProviders(approve=False)).run(objective["id"])

    def test_budget_is_durable(self):
        objective = self.objective(max_calls=1)
        with self.assertRaisesRegex(ValueError, "budget exhausted"):
            Runtime(self.store, FakeProviders()).run(objective["id"])
        self.assertEqual(self.store.get(objective["id"])["calls"], 1)

    def test_malformed_plan_blocks(self):
        objective = self.objective()
        with self.assertRaises(ValueError):
            Runtime(self.store, FakeProviders(malformed=True)).run(objective["id"])
        self.assertEqual(self.store.get(objective["id"])["status"], "blocked")

    def test_interrupted_state_requires_recovery(self):
        objective = self.objective()
        objective["status"] = "running"
        self.store.save(objective, "interrupted")
        with self.assertRaisesRegex(ValueError, "reconciliation"):
            Runtime(self.store, FakeProviders()).run(objective["id"])
        recover(self.store, objective["id"])
        result = Runtime(self.store, FakeProviders()).run(objective["id"], retry=True)
        self.assertEqual(result["status"], "completed")

    def test_live_process_prevents_recovery(self):
        objective = self.objective()
        objective["status"] = "running"
        self.store.save(objective, "interrupted")
        directory = self.store.home / "artifacts" / objective["id"] / "001"
        directory.mkdir(parents=True)
        (directory / "process.json").write_text(json.dumps({"pid": os.getpid()}))
        with self.assertRaisesRegex(ValueError, "may still be active"):
            recover(self.store, objective["id"])

    def test_second_supervisor_refused(self):
        with exclusive(self.store.home):
            with self.assertRaisesRegex(ValueError, "Another Capo"):
                with exclusive(self.store.home):
                    self.fail("lock acquired twice")

    def test_changes_validate_entire_batch_before_writing(self):
        with self.assertRaises(ValueError):
            apply_changes(self.repo, [{"path": "ok.txt", "content": "new", "delete": False},
                                     {"path": "../escape", "content": "oops", "delete": False}])
        self.assertFalse((self.repo / "ok.txt").exists())

    def test_symlink_and_protected_paths_rejected(self):
        (self.repo / "outside").symlink_to(self.root, target_is_directory=True)
        for name in ("outside/escape", ".git/config", ".github/workflows/publish.yml", ".env.local",
                     "../escape", "/tmp/escape", "CLAUDE.md"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                apply_changes(self.repo, [{"path": name, "content": "oops", "delete": False}])

    def test_snapshot_excludes_obvious_secrets(self):
        (self.repo / ".env").write_text("SECRET=do-not-send")
        (self.repo / "private.pem").write_text("private key")
        data = snapshot(self.repo)
        self.assertNotIn(".env", data["files"])
        self.assertNotIn("private.pem", data["files"])

    def test_dirty_source_refused(self):
        (self.repo / "maths.py").write_text("uncommitted work")
        with self.assertRaisesRegex(ValueError, "Commit or stash"):
            self.objective()

    def test_explicit_claude_reviewer(self):
        objective = self.objective()
        objective["workers"] = ["codex"]
        objective["reviewer"] = "claude"
        self.store.save(objective, "configured")
        fake = FakeProviders()
        result = Runtime(self.store, fake).run(objective["id"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual([p for p, _ in fake.calls], ["claude", "codex", "claude", "claude"])

    def test_plan_cannot_use_excluded_provider(self):
        objective = self.objective()
        objective["workers"] = ["grok"]
        self.store.save(objective, "configured")
        with self.assertRaisesRegex(ValueError, "outside the configured set"):
            Runtime(self.store, FakeProviders()).run(objective["id"])

    def test_plan_cannot_assign_implementation_to_reviewer(self):
        objective = self.objective()
        objective["reviewer"] = "codex"
        self.store.save(objective, "configured")
        with self.assertRaisesRegex(ValueError, "different providers"):
            Runtime(self.store, FakeProviders()).run(objective["id"])


class ProcessCase(unittest.TestCase):
    def test_timeout_terminates_process(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            with self.assertRaises(subprocess.TimeoutExpired):
                run_process([sys.executable, "-c", "import time; time.sleep(60)"],
                            directory, directory / "attempt", 0.1)
            pid = json.loads((directory / "attempt/process.json").read_text())["pid"]
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)

    def test_api_key_environment_is_not_forwarded(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"OPENAI_API_KEY": "fake"}):
            output = run_process([sys.executable, "-c", "import os; print('OPENAI_API_KEY' in os.environ)"],
                                 temp, Path(temp) / "attempt", 5)
            self.assertEqual(output.strip(), "False")

    def test_nonzero_exit_is_not_success(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(WorkerError):
                run_process([sys.executable, "-c", "raise SystemExit(4)"], temp, Path(temp) / "a", 5)

    def test_excessive_stderr_is_rejected_even_on_fast_exit(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(WorkerError, "log limit"):
                run_process([sys.executable, "-c", "import sys; sys.stderr.write('x'*8000001)"],
                            temp, Path(temp) / "a", 5)

    def test_claude_in_band_error(self):
        with tempfile.TemporaryDirectory() as temp, patch("capo.providers.run_process",
                return_value='{"is_error":true,"result":"quota"}'):
            with self.assertRaises(WorkerError):
                Providers().call("claude", "test", PLAN, Path(temp), Path(temp) / "a")

    def test_strict_contract_rejects_extra_fields_and_wrong_types(self):
        with self.assertRaises(ValueError):
            validate({"summary": "s", "acceptance": [], "tasks": [], "shell": "rm"}, PLAN)
        with self.assertRaises(ValueError):
            validate({"summary": "s", "acceptance": "not a list", "tasks": []}, PLAN)


if __name__ == "__main__":
    unittest.main()
