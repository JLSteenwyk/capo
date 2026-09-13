import tempfile
import unittest
from pathlib import Path

from capo.repository import git, snapshot


class ContextCase(unittest.TestCase):
    def test_task_files_are_included_before_unrelated_alphabetical_files(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            git(repo, "init")
            (repo / "aaa_noise.py").write_text("x" * 100)
            (repo / "z_report.py").write_text("report = True\n")
            (repo / "test_report.py").write_text("assert report\n")
            selected = snapshot(repo, limit=100, focus="Fix z_report.py and add report tests")
            self.assertIn("z_report.py", selected["files"])
            self.assertIn("test_report.py", selected["files"])
            self.assertIn("aaa_noise.py", selected["omitted"])
            self.assertLessEqual(sum(map(len, selected["files"].values())), 100)

    def test_focus_does_not_override_secret_path_exclusions(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            git(repo, "init")
            (repo / ".env").write_text("SYNTHETIC=value\n")
            selected = snapshot(repo, focus="Read .env")
            self.assertNotIn(".env", selected["files"])
            self.assertIn(".env", selected["omitted"])


if __name__ == "__main__":
    unittest.main()
