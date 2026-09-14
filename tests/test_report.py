"""Self-contained integration tests for the read-only objective report."""

import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch
from urllib.parse import unquote, urlsplit

from capo import report
from capo.store import Store


class ReportCase(unittest.TestCase):
    ID = "0123456789abcdef"
    OTHER_ID = "fedcba9876543210"
    PRIVATE = "SYNTHETIC_PRIVATE_REPORT_SENTINEL"

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.home = self.root / "state"
        self.home.mkdir()
        self.path = self.home / "capo.sqlite3"

    def objective(self, **changes):
        value = {
            "id": self.ID, "status": "queued", "team_name": "SPARKITscience",
            "calls": 0, "max_calls": 24, "round": 0, "max_rounds": 3,
            "active_stage": None, "request": self.PRIVATE,
            "prompt": self.PRIVATE, "workspace": self.PRIVATE,
            "error": self.PRIVATE, "credentials": {"token": self.PRIVATE},
        }
        value.update(changes)
        return value

    def ledger(self, objective=None, wal=False):
        db = sqlite3.connect(self.path)
        self.addCleanup(db.close)
        if wal:
            self.assertEqual(db.execute("PRAGMA journal_mode=WAL").fetchone(), ("wal",))
            db.execute("PRAGMA wal_autocheckpoint=0")
        db.execute("CREATE TABLE objectives (id TEXT PRIMARY KEY, data TEXT NOT NULL, updated REAL NOT NULL)")
        self.save(db, objective if objective is not None else self.objective())
        return db

    @staticmethod
    def save(db, value):
        db.execute("INSERT OR REPLACE INTO objectives VALUES (?, ?, ?)",
                   (value["id"], json.dumps(value), 1.0))
        db.commit()

    def files(self):
        """Include every source file, including SHM, but ignore read atime."""
        result = {}
        for path in sorted(self.root.rglob("*")):
            info = path.stat()
            result[str(path.relative_to(self.root))] = (
                "directory" if path.is_dir() else path.read_bytes(),
                info.st_dev, info.st_ino, info.st_size,
                info.st_mtime_ns, info.st_ctime_ns,
            )
        return result

    def invoke(self, identifier=None, home=None):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = report.main([
                self.ID if identifier is None else identifier,
                "--home", str(self.home if home is None else home),
            ])
        return code, out.getvalue(), err.getvalue()

    def success(self, result):
        code, out, err = result
        self.assertEqual(code, 0, err)
        self.assertEqual(err, "")
        self.assertNotIn(self.PRIVATE, out)
        value = json.loads(out)
        self.assertEqual(set(value), {
            "id", "status", "team_name", "calls", "max_calls", "round",
            "max_rounds", "active_stage", "verification", "reviewers", "pr_url",
        })
        self.assertEqual(set(value["verification"]), {
            "checks_passed", "checks_total", "reviews_approved", "reviews_total",
        })
        for reviewer in value["reviewers"]:
            self.assertEqual(set(reviewer), {"provider", "approved"})
            self.assertIs(type(reviewer["approved"]), bool)
        return value

    def failure(self, result, message=None):
        code, out, err = result
        self.assertNotEqual(code, 0)
        self.assertEqual(out, "")
        self.assertEqual(len(err.splitlines()), 1)
        self.assertTrue(err.startswith("capo-report: "))
        self.assertLess(len(err), 200)
        self.assertNotIn(str(self.root), err)
        self.assertNotIn(self.PRIVATE, err)
        self.assertNotIn("Traceback", err)
        if message:
            self.assertIn(message, err)

    def test_queued_before_verification(self):
        self.ledger().close()
        before = self.files()
        value = self.success(self.invoke())
        self.assertEqual(value, {
            "id": self.ID, "status": "queued", "team_name": "SPARKITscience",
            "calls": 0, "max_calls": 24, "round": 0, "max_rounds": 3,
            "active_stage": None,
            "verification": {"checks_passed": 0, "checks_total": 0,
                             "reviews_approved": 0, "reviews_total": 0},
            "reviewers": [], "pr_url": None,
        })
        self.assertEqual(self.files(), before)

    def test_real_store_lifecycle_preserves_source_and_omits_private_fields(self):
        # Use Store itself so schema or serialization changes cannot be hidden
        # by a hand-built fixture that merely agrees with report.py.
        store = Store(self.home)
        self.addCleanup(store.db.close)
        store.db.execute("PRAGMA wal_autocheckpoint=0")
        private = {
            "request": "SYNTHETIC_REQUEST_DO_NOT_DISCLOSE",
            "prompt": "SYNTHETIC_PROMPT_DO_NOT_DISCLOSE",
            "repo": "/synthetic/private/repository",
            "workspace": "/synthetic/private/workspace",
            "error": "SYNTHETIC_RAW_ERROR_DO_NOT_DISCLOSE",
            "finding": "SYNTHETIC_FINDING_DO_NOT_DISCLOSE",
            "token": "sk-SYNTHETIC-NOT-A-REAL-CREDENTIAL",
        }
        objective = store.create({
            "team_name": "SPARKITscience", "max_calls": 24, "max_rounds": 3,
            "request": private["request"], "prompt": private["prompt"],
            "repo": private["repo"], "workspace": private["workspace"],
            "error": private["error"], "credentials": {"token": private["token"]},
        })
        url = "https://github.com/example/project/pull/19"
        for status in ("queued", "completed", "blocked", "cancelled"):
            with self.subTest(status=status):
                objective["status"] = status
                if status == "queued":
                    objective.pop("verification", None)
                    objective.pop("publication", None)
                    expected_verification = {
                        "checks_passed": 0, "checks_total": 0,
                        "reviews_approved": 0, "reviews_total": 0,
                    }
                    expected_reviewers = []
                else:
                    approved = status == "completed"
                    objective.update(calls=4, round=1, active_stage="acceptance")
                    objective["verification"] = {
                        "checks": [{"passed": approved, "command": [private["prompt"]],
                                    "error": private["error"],
                                    "output": {"stderr.txt": private["token"]},
                                    "artifacts": private["workspace"]}],
                        "reviews": [{"provider": "grok", "approved": approved,
                                     "findings": [private["finding"]]}],
                        "decision": {"accepted": approved, "reason": private["request"]},
                    }
                    if approved:
                        objective["publication"] = {
                            "status": "published",
                            "payload": {"body": private["request"]},
                            "pr": {"url": url, "number": 19, "body": private["finding"]},
                        }
                    else:
                        objective.pop("publication", None)
                    expected_verification = {
                        "checks_passed": int(approved), "checks_total": 1,
                        "reviews_approved": int(approved), "reviews_total": 1,
                    }
                    expected_reviewers = [{"provider": "grok", "approved": approved}]
                store.save(objective, "synthetic_report_fixture")
                for suffix in ("", "-wal", "-shm"):
                    self.assertTrue(self.path.with_name("capo.sqlite3" + suffix).is_file())
                before = self.files()
                result = self.invoke(objective["id"])
                value = self.success(result)
                self.assertEqual(value["id"], objective["id"])
                self.assertEqual(value["status"], status)
                self.assertEqual(value["verification"], expected_verification)
                self.assertEqual(value["reviewers"], expected_reviewers)
                self.assertEqual(value["pr_url"], url if status == "completed" else None)
                for secret in private.values():
                    self.assertNotIn(secret, result[1] + result[2])
                # Includes DB/WAL/SHM bytes, existence, mtimes, and every other
                # entry under home, while the Store connection remains open.
                self.assertEqual(self.files(), before)

    def test_completed_report_redacts_nested_private_state(self):
        url = "https://github.com/example/project/pull/7"
        self.ledger(self.objective(
            status="completed", team_name="Example team", calls=8, round=1,
            active_stage="acceptance",
            verification={
                "checks": [{"passed": True, "command": [self.PRIVATE],
                            "output": self.PRIVATE, "artifacts": self.PRIVATE}],
                "reviews": [{"provider": "grok", "approved": True,
                             "findings": [self.PRIVATE]}],
                "decision": {"accepted": True, "reason": self.PRIVATE},
            },
            publication={"status": "published", "payload": {"body": self.PRIVATE},
                         "pr": {"url": url, "number": 7}},
        )).close()
        value = self.success(self.invoke())
        self.assertEqual(value["status"], "completed")
        self.assertEqual(value["team_name"], "Example team")
        self.assertEqual((value["calls"], value["round"]), (8, 1))
        self.assertEqual(value["verification"], {
            "checks_passed": 1, "checks_total": 1,
            "reviews_approved": 1, "reviews_total": 1,
        })
        self.assertEqual(value["reviewers"], [{"provider": "grok", "approved": True}])
        self.assertEqual(value["pr_url"], url)

    def test_blocked_with_failed_checks_and_rejected_review(self):
        self.ledger(self.objective(
            status="blocked", calls=24, round=3, active_stage="verification",
            verification={"checks": [{"passed": True},
                                     {"passed": False, "error": self.PRIVATE}],
                          "reviews": [{"provider": "grok", "approved": False,
                                       "findings": [self.PRIVATE]}]},
        )).close()
        value = self.success(self.invoke())
        self.assertEqual(value["status"], "blocked")
        self.assertEqual(value["active_stage"], "verification")
        self.assertEqual(value["verification"], {
            "checks_passed": 1, "checks_total": 2,
            "reviews_approved": 0, "reviews_total": 1,
        })
        self.assertEqual(value["reviewers"], [{"provider": "grok", "approved": False}])
        self.assertIsNone(value["pr_url"])

    def test_missing_state_creates_nothing(self):
        for home in (self.root / "missing" / "nested", self.home):
            with self.subTest(home=home.name):
                before = self.files()
                with patch.object(report.tempfile, "TemporaryDirectory") as temporary:
                    self.failure(self.invoke(home=home), "state")
                    temporary.assert_not_called()
                self.assertEqual(self.files(), before)

    def test_unknown_id_leaves_source_unchanged(self):
        self.ledger().close()
        before = self.files()
        self.failure(self.invoke(self.OTHER_ID), "unknown objective")
        self.assertEqual(self.files(), before)

    def test_invalid_ids_rejected_before_state_access(self):
        self.ledger().close()
        before = self.files()
        identifiers = ("", "' OR '1'='1", self.ID + "0", self.ID.upper(),
                       "0" * 15, self.ID + "; DROP TABLE objectives", self.ID + "\n")
        for identifier in identifiers:
            with self.subTest(identifier=repr(identifier)):
                with patch.object(report, "_read_row") as read_row:
                    self.failure(self.invoke(identifier), "invalid objective id")
                    read_row.assert_not_called()
                self.assertEqual(self.files(), before)

    def test_stale_journal_rejected_before_sqlite_connection(self):
        self.ledger(wal=True)
        self.path.with_name("capo.sqlite3-journal").write_bytes(b"synthetic stale journal")
        before = self.files()
        with patch.object(report.sqlite3, "connect") as connect:
            self.failure(self.invoke(), "stale rollback journal")
            connect.assert_not_called()
        self.assertEqual(self.files(), before)

    def test_closed_wal_ledger_does_not_create_sidecars(self):
        self.ledger(wal=True).close()
        self.assertEqual({p.name for p in self.home.iterdir()}, {"capo.sqlite3"})
        before = self.files()
        self.success(self.invoke())
        self.assertEqual(self.files(), before)

    def test_committed_wal_visible_uncommitted_update_excluded(self):
        db = self.ledger(wal=True)
        self.save(db, self.objective(calls=7, active_stage="reviewer"))
        self.assertGreater(self.path.with_name("capo.sqlite3-wal").stat().st_size, 0)
        db.execute("UPDATE objectives SET data = ? WHERE id = ?",
                   (json.dumps(self.objective(calls=8)), self.ID))
        before = self.files()
        value = self.success(self.invoke())
        self.assertEqual(value["calls"], 7)
        self.assertEqual(value["active_stage"], "reviewer")
        self.assertEqual(self.files(), before)
        db.rollback()

    def test_sqlite_only_opens_private_copy_with_readonly_parameterized_lookup(self):
        self.ledger(wal=True)
        before = self.files()
        original_connect = sqlite3.connect
        opened = []
        statements = []
        case = self

        class ObservedConnection(sqlite3.Connection):
            def execute(connection, sql, parameters=()):
                statements.append((sql, parameters))
                if sql.startswith("SELECT data FROM objectives"):
                    case.assertEqual(
                        sqlite3.Connection.execute(connection, "PRAGMA query_only").fetchone(),
                        (1,),
                    )
                return super().execute(sql, parameters)

        def connect(database, *args, **kwargs):
            parts = urlsplit(database)
            copy = Path(unquote(parts.path)).resolve()
            self.assertEqual(parts.scheme, "file")
            self.assertEqual(parts.query, "mode=ro")
            self.assertTrue(kwargs.get("uri"))
            self.assertFalse(copy.is_relative_to(self.home))
            self.assertEqual(copy.name, "capo.sqlite3")
            self.assertEqual(copy.parent.stat().st_mode & 0o777, 0o700)
            self.assertFalse(copy.with_name("capo.sqlite3-shm").exists())
            opened.append(copy)
            return original_connect(database, *args, factory=ObservedConnection, **kwargs)

        with patch.object(report.sqlite3, "connect", side_effect=connect):
            self.success(self.invoke())
        self.assertEqual(len(opened), 1)
        self.assertFalse(opened[0].parent.exists())
        self.assertIn(("PRAGMA quick_check", ()), statements)
        self.assertIn(("SELECT data FROM objectives WHERE id = ?", (self.ID,)), statements)
        self.assertEqual(self.files(), before)

    def test_commit_and_checkpoint_during_capture_retry_without_source_writes(self):
        # Inject a real second-connection write after the first DB read. This
        # deterministically exercises the race without scheduler-dependent sleeps.
        for checkpoint in (False, True):
            with self.subTest(checkpoint=checkpoint):
                home = self.root / ("checkpoint" if checkpoint else "commit")
                home.mkdir()
                old_home, old_path = self.home, self.path
                self.home, self.path = home, home / "capo.sqlite3"
                try:
                    self.ledger(wal=True)
                    writer = sqlite3.connect(self.path)
                    self.addCleanup(writer.close)
                    original_read = report._read_file
                    after_writer = []

                    def read_file(path, expected):
                        content = original_read(path, expected)
                        if path == self.path and not after_writer:
                            self.save(writer, self.objective(calls=9))
                            self.save(writer, self.objective(id=self.OTHER_ID))
                            if checkpoint:
                                self.assertEqual(writer.execute(
                                    "PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0], 0)
                            after_writer.append(self.files())
                        return content

                    with patch.object(report, "_read_file", side_effect=read_file) as reader:
                        value = self.success(self.invoke())
                    self.assertEqual(value["calls"], 9)
                    self.assertGreaterEqual(reader.call_count, 3)
                    self.assertEqual(self.files(), after_writer[0])
                    self.assertEqual(writer.execute("PRAGMA quick_check").fetchall(), [("ok",)])
                    self.assertEqual(writer.execute("SELECT COUNT(*) FROM objectives").fetchone(), (2,))
                finally:
                    self.home, self.path = old_home, old_path

    def test_unstable_snapshot_has_bounded_retries(self):
        self.ledger().close()
        before = self.files()
        with patch.object(report, "_read_file", side_effect=report.SnapshotChanged) as read_file:
            with patch.object(report.time, "sleep"):
                with patch.object(report.sqlite3, "connect") as connect:
                    self.failure(self.invoke(), "stable state snapshot")
                    connect.assert_not_called()
        self.assertEqual(read_file.call_count, report.SNAPSHOT_ATTEMPTS)
        self.assertEqual(self.files(), before)

    def test_size_limit_rejects_before_reading_or_creating_copy(self):
        self.ledger().close()
        before = self.files()
        with patch.object(report, "MAX_SNAPSHOT_BYTES", 1):
            with patch.object(report, "_read_file") as read_file:
                with patch.object(report.tempfile, "TemporaryDirectory") as temporary:
                    self.failure(self.invoke(), "size limit")
                    read_file.assert_not_called()
                    temporary.assert_not_called()
        self.assertEqual(self.files(), before)

    def test_corrupt_database_and_invalid_json_have_redacted_errors(self):
        self.path.write_bytes(b"not sqlite " + self.PRIVATE.encode())
        before = self.files()
        self.failure(self.invoke())
        self.assertEqual(self.files(), before)
        self.path.unlink()
        db = self.ledger()
        db.execute("UPDATE objectives SET data = ?", (self.PRIVATE,))
        db.commit()
        db.close()
        before = self.files()
        self.failure(self.invoke(), "invalid objective state")
        self.assertEqual(self.files(), before)


if __name__ == "__main__":
    unittest.main()
