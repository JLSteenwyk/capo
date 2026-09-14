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

    def test_referenced_large_source_has_bounded_read_only_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            git(repo, "init")
            source = "# unrelated padding\n" * 2000
            source += "def cancel_request():\n    return 'cancelled'\n"
            (repo / "handler.py").write_text(source)
            (repo / "README.md").write_text("Explain cancellation here.\n")
            selected = snapshot(repo, limit=1200,
                                focus="Update README.md about cancel_request in handler.py.")
            self.assertIn("README.md", selected["files"])
            self.assertNotIn("handler.py", selected["files"])
            self.assertIn("handler.py", selected["omitted"])
            excerpt = selected["supporting_excerpts"]["handler.py"]
            self.assertTrue(excerpt["read_only"])
            self.assertTrue(excerpt["incomplete"])
            self.assertIn("def cancel_request", str(excerpt))
            lines = source.splitlines(keepends=True)
            for item in excerpt["ranges"]:
                self.assertEqual(item["content"],
                                 "".join(lines[item["start_line"] - 1:item["end_line"]]))
            total = sum(map(len, selected["files"].values()))
            total += sum(len(item["content"]) for item in excerpt["ranges"])
            self.assertLessEqual(total, 1200)
            self.assertEqual((repo / "handler.py").read_text(), source)

    def test_excerpts_never_expand_path_access_or_guess_references(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            git(repo, "init")
            for name in (".env", "private.key", "ordinary.py", "app.py"):
                (repo / name).write_text("# context\n" * 4000)
            (repo / "link.py").symlink_to(repo / "ordinary.py")
            (repo / "binary.py").write_bytes(b"x" * 31000 + b"\0")
            (repo / "huge.py").write_text("x" * 2_000_001)
            selected = snapshot(repo, focus="Read .env private.key link.py binary.py huge.py other_app.py")
            self.assertEqual(selected["supporting_excerpts"], {})
            for name in (".env", "private.key", "link.py", "binary.py", "huge.py"):
                self.assertIn(name, selected["omitted"])

    def test_excerpts_share_global_budget_even_for_many_named_files(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            git(repo, "init")
            names = [f"module{index}.py" for index in range(12)]
            for name in names:
                (repo / name).write_text("def cancellation():\n    return True\n" * 1200)
            selected = snapshot(repo, limit=800, focus="cancellation " + " ".join(names))
            self.assertTrue(selected["supporting_excerpts"])
            contents = [item["content"] for excerpt in selected["supporting_excerpts"].values()
                        for item in excerpt["ranges"]]
            self.assertLessEqual(sum(map(len, contents)) +
                                 sum(map(len, selected["files"].values())), 800)
            self.assertEqual(set(selected["omitted"]), set(names))


if __name__ == "__main__":
    unittest.main()
