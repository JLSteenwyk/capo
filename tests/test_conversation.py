import hashlib
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from capo.conversation import ConversationError, ConversationPending, ConversationRouter


class ConversationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.router = ConversationRouter(self.home)
        self.context = {"message": "Does PhyKIT have issues?", "aliases": ["phykit"],
                        "objectives": [{"id": "abc", "alias": "phykit", "status": "queued"}],
                        "thread_objective_id": ""}
        self.route = {"action": "issues", "repository": "phykit", "objective_id": "", "reply": ""}

    def finish(self, router=None, event="event", context=None):
        router = router or self.router
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                return router.poll(event, context or self.context)
            except ConversationPending:
                time.sleep(.005)
        self.fail("Classifier did not complete")

    def test_async_cache_freezes_context_and_single_slot(self):
        entered, release = threading.Event(), threading.Event()
        def call(*args):
            entered.set()
            release.wait(2)
            return self.route
        with patch("capo.conversation.Providers") as provider:
            provider.return_value.call.side_effect = call
            with self.assertRaises(ConversationPending):
                self.router.poll("event", self.context)
            self.assertTrue(entered.wait(1))
            other = ConversationRouter(self.home)
            with self.assertRaises(ConversationPending):
                other.poll("second", self.context)
            self.assertFalse((self.router.root / hashlib.sha256(b"second").hexdigest()).exists())
            with self.assertRaises(ConversationPending):
                other.poll("event", self.context)
            release.set()
            self.assertEqual(self.finish(), self.route)
            self.assertEqual(other.poll("event", {"aliases": [], "objectives": []}), self.route)
            provider.assert_called_once_with(timeout=60)
            provider.return_value.call.assert_called_once()
            args = provider.return_value.call.call_args.args
            self.assertEqual(args[0], "claude")
            self.assertEqual(list(args[3].iterdir()), [])
            self.assertEqual(args[3].stat().st_mode & 0o777, 0o700)

    def test_restart_does_not_retry_started_attempt(self):
        directory = self.router.root / hashlib.sha256(b"event").hexdigest()
        directory.mkdir()
        (directory / "started.json").write_text('{}')
        with patch("capo.conversation.Providers") as provider:
            for _ in range(2):
                with self.assertRaisesRegex(ConversationError, "interrupted"):
                    ConversationRouter(self.home).poll("event", self.context)
            provider.assert_not_called()

    def test_provider_failure_redacted_and_cached(self):
        with patch("capo.conversation.Providers") as provider:
            provider.return_value.call.side_effect = RuntimeError("SECRET /private/path")
            for _ in range(2):
                with self.assertRaises(ConversationError) as error:
                    self.finish()
                self.assertNotIn("SECRET", str(error.exception))
                self.assertNotIn("/private", str(error.exception))
            provider.return_value.call.assert_called_once()
        value = json.loads(next(self.router.root.glob("*/outcome.json")).read_text())
        self.assertEqual(value, {"error": "failed"})

    def test_invalid_route_rejected(self):
        routes = [dict(self.route, action="approve"), dict(self.route, repository="other"),
                  dict(self.route, unexpected=True), dict(self.route, repository=""),
                  dict(self.route, action="cancel"), dict(self.route, reply="Invented facts"),
                  dict(self.route, action="reply", reply="")]
        with patch("capo.conversation.Providers") as provider:
            for i, route in enumerate(routes):
                provider.return_value.call.return_value = route
                with self.assertRaises(ConversationError):
                    self.finish(event=str(i))

    def test_hashed_event_path_and_private_receipt(self):
        with patch("capo.conversation.Providers") as provider:
            provider.return_value.call.return_value = self.route
            self.finish(event="../../secret-token")
        path = next(self.router.root.glob("*/outcome.json"))
        self.assertEqual(len(path.parent.name), 64)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual((path.parent / "started.json").stat().st_mode & 0o777, 0o600)

    def test_corrupt_cache_is_safe_error(self):
        directory = self.router.root / hashlib.sha256(b"event").hexdigest()
        directory.mkdir()
        (directory / "outcome.json").write_text("not JSON /private")
        with patch("capo.conversation.Providers") as provider:
            with self.assertRaises(ConversationError) as error:
                self.router.poll("event", self.context)
            self.assertNotIn("/private", str(error.exception))
            provider.assert_not_called()


if __name__ == "__main__":
    unittest.main()
