import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from capo.cli import main
from capo.repository import git
from capo.store import Store


class IssueIntakeCase(unittest.TestCase):
    def test_issue_improvement_is_frozen_and_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repo"
            repo.mkdir()
            git(repo, "init")
            git(repo, "config", "user.name", "Test")
            git(repo, "config", "user.email", "test@example.invalid")
            (repo / "capo").mkdir()
            (repo / "capo/runtime.py").write_text("# synthetic\n")
            (repo / "tests").mkdir()
            (repo / "tests/test_example.py").write_text("# synthetic\n")
            git(repo, "add", ".")
            git(repo, "commit", "-m", "fixture")
            config = root / "providers.json"
            config.write_text('{"grok":{"transport":"lima","vm":"test-vm"}}')
            argv = ["--home", str(root / "state"), "--providers-config", str(config),
                    "issue", "1", "--github", "owner/project", "--repo", str(repo),
                    "--check", "python3 -m unittest", "--self-improvement"]
            with patch("capo.cli.GitHub.issue", return_value={"title": "Improve", "body": "Synthetic issue",
                         "url": "https://github.com/owner/project/issues/1"}):
                first = io.StringIO()
                with contextlib.redirect_stdout(first):
                    self.assertEqual(main(argv), 0)
                second = io.StringIO()
                with contextlib.redirect_stdout(second):
                    self.assertEqual(main(argv), 0)
            one, two = json.loads(first.getvalue()), json.loads(second.getvalue())
            self.assertEqual(one["id"], two["id"])
            self.assertEqual(one["regression_baseline"], two["regression_baseline"])
            self.assertEqual(two["status"], "queued")
            self.assertEqual(two["providers_config"]["grok"]["vm"], "test-vm")
            store = Store(root / "state")
            self.assertEqual(len(store.list()), 1)
            store.db.close()


if __name__ == "__main__":
    unittest.main()
