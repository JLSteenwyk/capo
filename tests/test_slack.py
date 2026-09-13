import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import patch

from capo.repository import git
from capo.slack import SlackService, authorized, configure, ingest, validate_config
from capo.store import Store


class Client:
    def __init__(self):
        self.messages = []

    def chat_postMessage(self, **kwargs):
        self.messages.append(kwargs)
        return {"ts": "123.456"}


class SlackCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-b", "main")
        git(self.repo, "config", "user.name", "Test")
        git(self.repo, "config", "user.email", "test@example.invalid")
        (self.repo / "hello.py").write_text("print('hello')\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "fixture")
        self.config = {"team_id": "T123", "channel_id": "C123", "owner_user_id": "U123",
            "team_name": "SPARKITscience", "auto_run": False,
            "repositories": {"project": {"path": str(self.repo), "checks": ["python3 hello.py"],
                                         "workers": ["codex"], "reviewer": "claude"}}}
        self.store = Store(self.root / "state")
        self.client = Client()
        self.service = SlackService(self.store, self.config, self.client)

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def body(self, text="project: improve the greeting", event_id="Ev123"):
        return {"team_id": "T123", "event_id": event_id, "event": {
            "type": "app_mention", "channel": "C123", "user": "U123",
            "text": "<@UBOT> " + text, "ts": "123.456"}}

    def test_only_owner_in_configured_workspace_and_channel_is_authorized(self):
        self.assertTrue(authorized(self.config, self.body()))
        for key, value in (("user", "UOTHER"), ("channel", "COTHER"), ("bot_id", "B123"),
                           ("subtype", "message_changed")):
            body = self.body()
            body["event"][key] = value
            self.assertFalse(ingest(self.store.home, self.config, body))
        body = self.body()
        body["team_id"] = "TOTHER"
        self.assertFalse(ingest(self.store.home, self.config, body))
        self.assertEqual(self.store.pending_slack(), [])

    def test_duplicate_delivery_creates_one_objective_and_one_reply(self):
        for _ in range(2):
            ingest(self.store.home, self.config, self.body())
        self.service.tick()
        self.assertEqual(len(self.store.list()), 1)
        self.assertEqual(len(self.client.messages), 1)
        row = self.store.list()[0]
        self.assertEqual(row["team_name"], "SPARKITscience")
        self.assertEqual(row["request"], "improve the greeting")
        self.assertEqual(self.client.messages[0]["thread_ts"], "123.456")
        self.assertEqual(row["status"], "queued")

    def test_crash_after_queue_before_ack_reuses_objective(self):
        for _ in range(2):
            self.service.dispatch("Ev123", self.body())
        self.assertEqual(len(self.store.list()), 1)

    def test_changed_policy_is_checked_again_before_dispatch(self):
        ingest(self.store.home, self.config, self.body())
        self.config["owner_user_id"] = "UNEW"
        self.service.tick()
        self.assertEqual(self.store.list(), [])
        self.assertEqual(self.client.messages, [])

    def test_cannot_read_objective_from_another_channel(self):
        self.service.dispatch("Ev123", self.body())
        objective = self.store.list()[0]
        objective["slack"]["channel_id"] = "COTHER"
        self.store.save(objective, "fixture")
        with self.assertRaisesRegex(ValueError, "does not belong"):
            self.service.dispatch("Ev456", self.body("status " + objective["id"]))

    def test_unknown_alias_cannot_become_an_arbitrary_path(self):
        with self.assertRaisesRegex(ValueError, "Unknown repository"):
            self.service.dispatch("Ev123", self.body("elsewhere: change things"))
        self.assertEqual(self.store.list(), [])

    def test_cancel_queued_objective(self):
        self.service.dispatch("Ev123", self.body())
        objective = self.store.list()[0]
        self.service.dispatch("Ev456", self.body("cancel " + objective["id"]))
        self.assertEqual(self.store.get(objective["id"])["status"], "cancelled")

    def test_self_improvement_requires_explicit_repo_configuration(self):
        with self.assertRaisesRegex(ValueError, "not enabled"):
            self.service.dispatch("Ev123", self.body("improve project: change things"))

    def test_reply_does_not_interpret_mentions(self):
        self.service.reply(self.body()["event"], "<@UOTHER> & <!channel>")
        self.assertEqual(self.client.messages[0]["text"], "&lt;@UOTHER&gt; &amp; &lt;!channel&gt;")
        self.assertFalse(self.client.messages[0]["mrkdwn"])

    def test_run_child_does_not_inherit_slack_tokens(self):
        self.service.dispatch("Ev123", self.body())
        self.config["auto_run"] = True
        with patch.dict("os.environ", {"SLACK_APP_TOKEN": "fake", "SLACK_BOT_TOKEN": "fake"}), \
             patch("capo.slack.subprocess.Popen") as process:
            self.service.tick()
            self.assertNotIn("SLACK_APP_TOKEN", process.call_args.kwargs["env"])
            self.assertNotIn("SLACK_BOT_TOKEN", process.call_args.kwargs["env"])

    def test_configuration_refuses_empty_owner(self):
        self.config["owner_user_id"] = ""
        with self.assertRaises(ValueError):
            validate_config(self.config)

    def test_setup_resolves_and_pins_ids_without_storing_tokens(self):
        config_path = self.root / "slack.json"
        config = dict(self.config, workspace_name="SPARKITscience", channel_name="capo", team_id="", channel_id="")
        config_path.write_text(json.dumps(config))
        class SetupClient:
            def auth_test(self):
                return {"team": "SPARKITscience", "team_id": "T123"}
            def conversations_list(self, **kwargs):
                return {"channels": [{"name": "capo", "id": "C123", "is_member": True}]}
        resolved = configure(config_path, SetupClient())
        self.assertEqual(resolved["channel_id"], "C123")
        self.assertEqual(config_path.stat().st_mode & 0o777, 0o600)
        self.assertNotIn("token", config_path.read_text())

    def test_setup_rejects_wrong_workspace(self):
        config_path = self.root / "slack.json"
        config_path.write_text(json.dumps(dict(self.config, workspace_name="SPARKITscience", channel_name="capo")))
        class SetupClient:
            def auth_test(self):
                return {"team": "Other workspace", "team_id": "TOTHER"}
        with self.assertRaisesRegex(ValueError, "different named workspace"):
            configure(config_path, SetupClient())


if __name__ == "__main__":
    unittest.main()
