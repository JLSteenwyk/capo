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

    def test_issue_link_intake_fetches_context_once_and_preserves_source(self):
        git(self.repo, "remote", "add", "origin", "https://github.com/example/project.git")
        self.config["repositories"]["project"]["github_auth"] = "keyring"
        body = self.body("project: Fix <https://github.com/example/project/issues/12|issue #12>")
        with patch("capo.github.GitHub.issue", return_value={"title": "A real task", "body": "Acceptance details"}) as issue:
            self.service.dispatch("Ev123", body)
            self.service.dispatch("Ev123", body)
        issue.assert_called_once_with("example/project", 12)
        objective = self.store.list()[0]
        self.assertIn("Acceptance details", objective["request"])
        self.assertEqual(objective["source"], "slack:T123:Ev123")

    def test_issue_context_is_added_to_paused_thread_followup(self):
        git(self.repo, "remote", "add", "origin", "https://github.com/example/project.git")
        self.service.dispatch("Ev123", self.body())
        objective = self.store.list()[0]
        objective.update(status="awaiting_input", question="Which issue?")
        self.store.save(objective, "fixture")
        body = self.body("followup: https://github.com/example/project/issues/12", "Ev456")
        body["event"]["thread_ts"] = "123.456"
        with patch("capo.github.GitHub.issue", return_value={"title": "Task", "body": "Details"}) as issue:
            self.service.dispatch("Ev456", body)
            self.service.dispatch("Ev456", body)
        issue.assert_called_once()
        self.assertEqual(self.store.get(objective["id"])["status"], "queued")
        self.assertIn("Details", self.store.followups(objective["id"])[0]["text"])

    def test_issue_link_policy_and_fetch_failures_create_no_objectives(self):
        git(self.repo, "remote", "add", "origin", "https://github.com/example/project.git")
        with patch("capo.github.GitHub.issue") as issue:
            with self.assertRaisesRegex(ValueError, "selected repository"):
                self.service.dispatch("Ev123", self.body("project: https://github.com/other/project/issues/1"))
            body = self.body("project: https://github.com/example/project/issues/1")
            body["event"]["user"] = "UOTHER"
            with self.assertRaisesRegex(ValueError, "Unauthorized"):
                self.service.dispatch("Ev123", body)
            issue.assert_not_called()
            issue.side_effect = RuntimeError("Unavailable")
            with self.assertRaisesRegex(RuntimeError, "Unavailable"):
                self.service.dispatch("Ev123", self.body("project: https://github.com/example/project/issues/1"))
        self.assertEqual(self.store.list(), [])

    def test_issue_context_limits_and_unrelated_urls(self):
        from capo.slack import resolve_issue_links
        settings = self.config["repositories"]["project"]
        git(self.repo, "remote", "add", "origin", "https://github.com/example/project.git")
        with patch("capo.github.GitHub.issue") as issue:
            request = "Read https://example.org/info and https://github.com.evil/example/project/issues/1"
            self.assertEqual(resolve_issue_links(settings, request), request)
            with self.assertRaisesRegex(ValueError, "At most three"):
                resolve_issue_links(settings, " ".join(f"https://github.com/example/project/issues/{n}" for n in range(1,5)))
            issue.assert_not_called()
            issue.return_value = {"title": "Task", "body": "x" * 50001}
            with self.assertRaisesRegex(ValueError, "50000"):
                resolve_issue_links(settings, "https://github.com/example/project/issues/1")

    def test_plain_text_reply_preserves_apostrophes_and_escapes_mentions(self):
        self.service.reply(self.body()["event"], "The issue's title <@UOTHER> & details")
        self.assertEqual(self.client.messages[-1]["text"], "The issue's title &lt;@UOTHER&gt; &amp; details")

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

    def test_thread_followup_is_durable_and_deduplicated(self):
        self.service.dispatch("Ev123", self.body())
        objective = self.store.list()[0]
        body = self.body("clarify: Keep the greeting short", "Ev456")
        body["event"]["thread_ts"] = "123.456"
        self.service.dispatch("Ev456", body)
        self.service.dispatch("Ev456", body)
        self.assertEqual(self.store.followups(objective["id"]),
                         [{"event_id": "Ev456", "text": "Keep the greeting short"}])
        self.assertEqual(len(self.store.list()), 1)

    def test_followup_after_completion_is_not_mistaken_for_runner_failure(self):
        from unittest.mock import Mock
        self.service.dispatch("Ev123", self.body())
        objective = self.store.list()[0]
        objective["status"] = "completed"
        self.store.save(objective, "fixture")
        self.store.add_followup(objective["id"], "EvFollowup", "Also cover empty input")
        self.service.active = Mock(pid=1234)
        self.service.active.poll.return_value = 0
        self.service.active_id = objective["id"]
        self.service.tick()
        self.assertEqual(self.store.get(objective["id"])["status"], "queued")
        self.assertIn("follow-up is queued", self.client.messages[-1]["text"])

    def test_terminal_checkpoint_between_read_and_poll_is_preserved(self):
        from unittest.mock import Mock
        self.service.dispatch("Ev123", self.body())
        identifier = self.store.list()[0]["id"]
        for status in ("completed", "awaiting_input"):
            with self.subTest(status=status):
                objective = self.store.get(identifier)
                objective.update(status="running", supervisor_pid=1234)
                self.store.save(objective, "fixture_running")
                def finish_during_poll():
                    completed = self.store.get(identifier)
                    completed.update(status=status, question="Which input format?")
                    self.store.save(completed, "fixture_terminal")
                    return 0
                self.service.active = Mock(pid=1234)
                self.service.active.poll.side_effect = finish_during_poll
                self.service.active_id = identifier
                self.service.tick()
                self.assertEqual(self.store.get(identifier)["status"], status)
                self.assertNotIn("Runner exited without", self.client.messages[-1]["text"])

    def test_clarification_is_reported_and_answer_requeues_objective(self):
        from unittest.mock import Mock
        self.service.dispatch("Ev123", self.body())
        objective = self.store.list()[0]
        objective.update(status="awaiting_input", question="Should empty input return zero?")
        self.store.save(objective, "clarification_requested")
        self.service.active = Mock(pid=1234)
        self.service.active.poll.return_value = 0
        self.service.active_id = objective["id"]
        self.service.tick()
        self.assertIn("Should empty input return zero?", self.client.messages[-1]["text"])
        self.assertEqual(self.store.get(objective["id"])["status"], "awaiting_input")
        body = self.body("Yes, return zero", "EvAnswer")
        body["event"]["thread_ts"] = "123.456"
        self.service.dispatch("EvAnswer", body)
        self.assertEqual(self.store.get(objective["id"])["status"], "queued")
        self.assertEqual(self.store.followups(objective["id"])[0]["text"], "Yes, return zero")

    def test_restart_discovers_terminal_checkpoint_without_child_handle(self):
        self.service.dispatch("Ev123", self.body())
        objective = self.store.list()[0]
        objective.update(status="awaiting_input", question="Which input format?")
        self.store.save(objective, "clarification_requested")
        home = self.store.home
        self.store.db.close()
        self.store = Store(home)
        restarted = SlackService(self.store, self.config, self.client)
        restarted.tick()
        self.assertEqual(len(self.client.messages), 1)
        self.assertIn("Which input format?", self.client.messages[0]["text"])
        self.assertEqual(self.client.messages[0]["thread_ts"], "123.456")
        self.assertEqual(self.store.get(objective["id"]), objective)
        SlackService(self.store, self.config, self.client).tick()
        self.assertEqual(len(self.client.messages), 1)
        objective.update(status="completed", calls=5)
        self.store.save(objective, "accepted")
        with patch("capo.slack.time.time", return_value=10**12):
            SlackService(self.store, self.config, self.client).tick()
        self.assertEqual(len(self.client.messages), 2)
        self.assertIn("Verified changes", self.client.messages[-1]["text"])

    def test_terminal_delivery_rate_limit_and_chunks_survive_restart(self):
        self.service.dispatch("Ev123", self.body())
        objective = self.store.list()[0]
        objective.update(status="awaiting_input", question="Q" * 2700)
        self.store.save(objective, "clarification_requested")
        from types import SimpleNamespace
        failure = RuntimeError("synthetic rate limit")
        failure.response = SimpleNamespace(headers={"Retry-After": "30"})
        with patch("capo.slack.time.time", return_value=100):
            with patch.object(self.client, "chat_postMessage", side_effect=failure):
                with self.assertRaises(RuntimeError):
                    self.service.tick()
        restarted = SlackService(self.store, self.config, self.client)
        with patch("capo.slack.time.time", return_value=129):
            restarted.tick()
        self.assertEqual(self.client.messages, [])
        with patch("capo.slack.time.time", return_value=130):
            restarted.tick()
        self.assertEqual(len(self.client.messages), 1)
        with patch("capo.slack.time.time", return_value=132):
            SlackService(self.store, self.config, self.client).tick()
        self.assertEqual(len(self.client.messages), 2)
        self.assertEqual("".join(m["text"] for m in self.client.messages).count("Q"), 2700)
        with patch("capo.slack.time.time", return_value=134):
            SlackService(self.store, self.config, self.client).tick()
        self.assertEqual(len(self.client.messages), 2)

    def test_undelivered_question_is_suppressed_after_followup_or_policy_change(self):
        self.service.dispatch("Ev123", self.body())
        objective = self.store.list()[0]
        objective.update(status="awaiting_input", question="Stale question?")
        self.store.save(objective, "clarification_requested")
        with patch("capo.slack.time.time", return_value=100):
            self.service.delivery_delay(30)
            self.service.tick()
        self.assertEqual(self.client.messages, [])
        self.store.add_followup(objective["id"], "EvAnswer", "Answer already supplied")
        with patch("capo.slack.time.time", return_value=140):
            SlackService(self.store, self.config, self.client).tick()
        self.assertEqual(self.client.messages, [])
        objective = self.store.get(objective["id"])
        objective.update(status="completed")
        self.store.save(objective, "accepted")
        for key in ("owner_user_id", "channel_id", "team_id"):
            with self.subTest(key=key):
                config = dict(self.config, **{key: self.config[key][0] + "OTHER"})
                SlackService(self.store, config, self.client).tick()
        self.assertEqual(self.client.messages, [])

    def test_dispatch_itself_rechecks_owner(self):
        body = self.body()
        body["event"]["user"] = "UOTHER"
        with self.assertRaisesRegex(ValueError, "Unauthorized"):
            self.service.dispatch("Ev123", body)

    def test_publication_requires_explicit_configuration(self):
        self.service.dispatch("Ev123", self.body())
        identifier = self.store.list()[0]["id"]
        with self.assertRaisesRegex(ValueError, "not enabled"):
            self.service.dispatch("Ev456", self.body("prepare " + identifier))

    def test_approval_requires_successfully_delivered_preview_and_exact_digest(self):
        self.service.dispatch("Ev123", self.body())
        objective = self.store.list()[0]
        identifier = objective["id"]
        self.config["repositories"]["project"]["allow_publication"] = True
        git(self.repo, "remote", "add", "origin", "https://github.com/example/project.git")
        digest = "a" * 64
        publication = {"digest": digest, "status": "prepared", "payload": {
            "repository": "example/project", "base_branch": "main", "commit": "b" * 40,
            "title": "Improve greeting", "body": "One check passed."}}
        objective["publication"] = publication
        self.store.save(objective, "fixture")
        directory = self.store.home / "artifacts" / identifier
        directory.mkdir(parents=True)
        (directory / "changes.patch").write_text("-hello\n+hello world\n")
        with patch("capo.github.publish") as publish:
            with self.assertRaisesRegex(ValueError, "First request"):
                self.service.dispatch("Ev456", self.body(f"approve {identifier} {digest}"))
            publish.assert_not_called()
        ingest(self.store.home, self.config, self.body("prepare " + identifier, "EvPrepare"))
        with patch("capo.github.prepare", return_value=publication):
            self.service.process_messages()
        self.assertIn("+hello world", self.client.messages[-1]["text"])
        self.assertEqual(self.store.get(identifier)["slack_review_digest"], digest)
        with patch("capo.github.publish", return_value={"pr": {"url": "https://github.com/example/project/pull/1"}}) as publish:
            self.service.dispatch("EvApprove", self.body(f"approve {identifier} {digest}"))
            self.assertEqual(publish.call_args.args[2], digest)
            with self.assertRaisesRegex(ValueError, "First request"):
                self.service.dispatch("EvBad", self.body(f"approve {identifier} {'c' * 64}"))

    def test_failed_preview_delivery_does_not_authorize_publication(self):
        self.service.dispatch("Ev123", self.body())
        objective = self.store.list()[0]
        objective["publication"] = {"digest": "a" * 64}
        self.store.save(objective, "fixture")
        ingest(self.store.home, self.config, self.body("help", "EvPreview"))
        def dispatch(*_):
            self.service.pending_review = (objective["id"], "a" * 64)
            return "Review the candidate"
        with patch.object(self.service, "dispatch", side_effect=dispatch), \
             patch.object(self.service, "reply", side_effect=RuntimeError("delivery failed")):
            with self.assertRaises(RuntimeError):
                self.service.process_messages()
        self.assertNotIn("slack_review_digest", self.store.get(objective["id"]))
        self.assertTrue(self.store.pending_slack())

    def test_large_replies_are_sent_completely_without_truncation(self):
        text = "<" * 7000
        self.service.reply(self.body()["event"], text)
        self.assertEqual("".join(row["text"] for row in self.client.messages), "&lt;" * 7000)
        self.assertTrue(all(len(row["text"]) <= 15000 for row in self.client.messages))

    def test_preview_resumes_after_rate_limit_and_restart_before_approval(self):
        self.service.dispatch("Ev123", self.body())
        objective = self.store.list()[0]
        identifier = objective["id"]
        digest = "d" * 64
        objective["publication"] = {"digest": digest}
        self.store.save(objective, "fixture")
        ingest(self.store.home, self.config, self.body("help", "EvPreviewResume"))
        preview = "A" * 2500 + "B" * 2500 + "C" * 10
        def dispatch(*_):
            self.service.pending_review = (identifier, digest)
            return preview
        class RateLimited(RuntimeError):
            response = type("Response", (), {"headers": {"Retry-After": "30"}})()
        with patch("capo.slack.time.time", return_value=100), \
             patch.object(self.service, "dispatch", side_effect=dispatch):
            self.service.process_messages()
        self.assertEqual(len(self.client.messages), 1)
        self.assertNotIn("slack_review_digest", self.store.get(identifier))
        with patch("capo.slack.time.time", return_value=101), \
             patch.object(self.client, "chat_postMessage", side_effect=RateLimited("rate limited")):
            with self.assertRaises(RateLimited):
                self.service.process_messages()
        restarted = SlackService(self.store, self.config, self.client)
        with patch("capo.slack.time.time", return_value=130), \
             patch.object(restarted, "dispatch", side_effect=AssertionError("must not repeat dispatch")):
            restarted.process_messages()
        self.assertEqual(len(self.client.messages), 1)
        for now in (131, 132):
            with patch("capo.slack.time.time", return_value=now):
                restarted.process_messages()
        self.assertEqual([message["text"] for message in self.client.messages],
                         ["A" * 2500, "B" * 2500, "C" * 10])
        self.assertEqual(self.store.get(identifier)["slack_review_digest"], digest)
        self.assertEqual(self.store.pending_slack(), [])

    def test_stale_partial_preview_never_enables_old_approval(self):
        self.service.dispatch("Ev123", self.body())
        objective = self.store.list()[0]
        identifier = objective["id"]
        objective["publication"] = {"digest": "d" * 64}
        self.store.save(objective, "fixture")
        ingest(self.store.home, self.config, self.body("help", "EvStalePreview"))
        def dispatch(*_):
            self.service.pending_review = (identifier, "d" * 64)
            return "A" * 3000
        with patch("capo.slack.time.time", return_value=100), \
             patch.object(self.service, "dispatch", side_effect=dispatch):
            self.service.process_messages()
        objective.pop("publication")
        self.store.save(objective, "invalidated_fixture")
        with patch("capo.slack.time.time", return_value=101):
            self.service.process_messages()
        self.assertIn("candidate changed", self.client.messages[-1]["text"])
        self.assertNotIn("slack_review_digest", self.store.get(identifier))
        self.assertEqual(self.store.pending_slack(), [])

    def test_restarted_service_does_not_spawn_during_live_supervisor_checkpoint(self):
        from capo.runtime import exclusive
        self.service.dispatch("Ev123", self.body())
        self.config["auto_run"] = True
        # Runtime holds this lock across its queued checkpoints and live calls.
        with exclusive(self.store.home), patch("capo.slack.subprocess.Popen") as process:
            restarted = SlackService(self.store, self.config, self.client)
            restarted.tick()
            process.assert_not_called()
        with patch("capo.slack.subprocess.Popen") as process:
            restarted.tick()
            process.assert_called_once()

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
