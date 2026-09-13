import argparse
import tempfile
import threading
import time
import unittest
from pathlib import Path

from capo.cli import add_objective
from capo.process import run_process
from capo.repository import git
from capo.runtime import Runtime, exclusive
from capo.slack import SlackService
from capo.store import Store
from test_capo import FakeProviders
from test_slack import Client


class CancellationCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-b", "main")
        git(self.repo, "config", "user.name", "Test")
        git(self.repo, "config", "user.email", "test@example.invalid")
        (self.repo / "maths.py").write_text("def add(a,b):\n    return a-b\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "fixture")
        self.store = Store(self.root / "state")
        self.objective = add_objective(self.store, argparse.Namespace(repo=self.repo,
            check=['python3 -c "from maths import add; assert add(2,3)==5"']), "Fix addition")

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def test_restarted_slack_requests_cancellation_during_queued_checkpoint(self):
        config = {"team_id": "T123", "channel_id": "C123", "owner_user_id": "U123",
            "auto_run": False, "repositories": {"project": {"path": str(self.repo), "checks": ["true"]}}}
        self.objective["slack"] = dict(team_id="T123", channel_id="C123", owner_user_id="U123",
                                      ts="123.456", thread_ts="123.456", repository_alias="project")
        self.store.save(self.objective, "fixture_slack")
        service = SlackService(self.store, config, Client())
        body = {"team_id": "T123", "event": {"type": "app_mention", "channel": "C123", "user": "U123",
            "ts": "123.456", "text": "<@UBOT> cancel " + self.objective["id"]}}
        with exclusive(self.store.home):
            response = service.dispatch("EvCancel", body)
        self.assertIn("Cancellation requested", response)
        self.assertEqual(self.store.get(self.objective["id"])["status"], "queued")
        self.assertEqual(len(self.store.cancellation_requests(self.objective["id"])), 1)
        # An unrelated subsequent checkpoint cannot erase the request.
        self.store.save(self.objective, "later_checkpoint")
        with self.assertRaises(KeyboardInterrupt):
            Runtime(self.store, FakeProviders()).run(self.objective["id"])
        self.assertEqual(self.store.get(self.objective["id"])["status"], "cancelled")
        self.assertEqual(self.store.get(self.objective["id"])["calls"], 0)

    def test_active_provider_stops_and_explicit_retry_clears_old_request(self):
        import sys
        started = threading.Event()
        failures = []
        class SlowProviders(FakeProviders):
            def call(inner, provider, prompt, schema, cwd, directory):
                if "implementer for" in prompt:
                    started.set()
                    run_process([sys.executable, "-c", "import time; time.sleep(60)"], cwd, directory, 65)
                return super().call(provider, prompt, schema, cwd, directory)
        def cancel():
            if not started.wait(10):
                failures.append("provider never started")
                return
            other = Store(self.store.home)
            try:
                other.request_cancellation(self.objective["id"], "EvCancel")
            finally:
                other.db.close()
        requester = threading.Thread(target=cancel)
        requester.start()
        began = time.monotonic()
        try:
            with self.assertRaises(KeyboardInterrupt):
                Runtime(self.store, SlowProviders()).run(self.objective["id"])
        finally:
            requester.join(timeout=12)
        self.assertFalse(failures)
        self.assertLess(time.monotonic() - began, 10)
        self.assertEqual(self.store.get(self.objective["id"])["status"], "cancelled")
        result = Runtime(self.store, FakeProviders()).run(self.objective["id"], retry=True)
        self.assertEqual(result["status"], "completed")
        self.assertFalse(self.store.cancellation_requests(self.objective["id"]))

    def test_followup_resumes_cancelled_work_without_replaying_old_cancel(self):
        self.store.request_cancellation(self.objective["id"], "EvCancel")
        objective = self.store.get(self.objective["id"])
        objective["status"] = "cancelled"
        self.store.save(objective, "fixture_cancelled")
        updated = self.store.add_followup(objective["id"], "EvContinue", "Continue with the fix")
        self.assertEqual(updated["status"], "queued")
        self.assertFalse(self.store.cancellation_requests(objective["id"]))
        self.assertFalse(self.store.request_cancellation(objective["id"], "EvCancel"))
        self.assertFalse(self.store.cancellation_requests(objective["id"]))
        self.assertTrue(self.store.request_cancellation(objective["id"], "EvNewCancel"))

    def test_duplicate_request_is_durable_without_duplicate_events(self):
        for _ in range(2):
            self.store.request_cancellation(self.objective["id"], "EvCancel")
        self.assertEqual(len(self.store.cancellation_requests(self.objective["id"])), 1)
        events = self.store.events(self.objective["id"])
        self.assertEqual(sum(event["kind"] == "cancellation_requested" for event in events), 1)
