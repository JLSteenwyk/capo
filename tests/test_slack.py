import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import patch

from capo.repository import git
from capo.slack import SlackService, authorized, configure, ingest, validate_config, HELP
from capo.store import Store


class Client:
    def __init__(self):
        self.messages = []

    def chat_postMessage(self, **kwargs):
        self.messages.append(kwargs)
        return {"ts": "123.456"}


class SlackCase(unittest.TestCase):
    def setUp(self):
        # These fixtures exercise commands and the preserved legacy classifier
        # path. New ordinary routing has separate shared-loop integration tests.
        legacy_patch = patch('capo.slack.legacy_conversation', return_value=True)
        legacy_patch.start()
        self.addCleanup(legacy_patch.stop)
        router_patch = patch("capo.conversation.ConversationRouter")
        self.router = router_patch.start().return_value
        self.addCleanup(router_patch.stop)
        self.router.poll.side_effect = lambda event_id, context: {
            "action": "followup" if context["thread_objective_id"] else "reply",
            "repository": "", "objective_id": context["thread_objective_id"],
            "reply": "Which repository?" if not context["thread_objective_id"] else ""}
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

    def test_owner_image_reply_file_share_subtype_is_accepted(self):
        original = self.body()
        self.assertTrue(ingest(self.store.home, self.config, original))
        reply = self.body("Here is the screenshot", "image-reply")
        reply["event"].update(type="message", subtype="file_share", thread_ts="123.456", ts="124.456",
                              text="Here is the screenshot", files=[{"id":"F123", "mimetype":"image/png"}])
        self.assertTrue(authorized(self.config, reply, self.store))
        self.assertTrue(ingest(self.store.home, self.config, reply))
        reply["event"]["user"]="UOTHER"
        self.assertFalse(authorized(self.config, reply, self.store))

    def test_legacy_digest_thread_accepts_only_owner_feedback_without_mention(self):
        from capo.digest import DigestStore, scope
        from datetime import datetime, timezone
        db = DigestStore(self.store.home)
        owner = scope(self.config)
        run = {"key": "digest-test", "scope": owner, "day": "2026-09-14", "status": "sent",
               "ts": "100.1", "payload": {"news": []}}
        db.save(run, datetime.now(timezone.utc)); db.close()
        body = self.body("more AI news")
        body["event"].update(type="message", text="more AI news", ts="101.1", thread_ts="100.1")
        self.assertTrue(authorized(self.config, body, self.store))
        import hashlib
        from capo.conversation import _write
        marker=self.store.home/'digest-feedback'/'conversation'/hashlib.sha256(b'digest-event').hexdigest()
        marker.mkdir(parents=True)
        _write(marker/'started.json', {'context': {}})
        with patch("capo.digest_feedback.FeedbackConversation") as router:
            router.return_value.poll.return_value = {"reply": "I’ll show more AI."}
            self.assertEqual(self.service.dispatch("digest-event", body), "I’ll show more AI.")
            body["event"]["user"] = "UOTHER"
            self.assertFalse(authorized(self.config, body, self.store))
            with self.assertRaises(ValueError):
                self.service.dispatch("unauthorized", body)
            router.return_value.poll.assert_called_once()

    def test_specialist_delegation_preserves_owner_boundary(self):
        self.router.poll.side_effect = None
        self.router.poll.return_value = {'action':'money_saver','repository':'','objective_id':'','reply':''}
        with patch('capo.team.dispatch', return_value='Here are the savings.') as specialist:
            body=self.body('Review these subscription costs.')
            self.assertEqual(self.service.dispatch('team-request',body),'Here are the savings.')
            self.assertEqual(specialist.call_args.args[2],'money_saver')
            self.assertEqual(specialist.call_args.args[3]['message'],'Review these subscription costs.')
            self.assertFalse(self.router.poll.call_args.args[1]['team']['connections']['email'])
            body['event']['user']='OTHER'
            with self.assertRaises(ValueError):self.service.dispatch('other',body)
            specialist.assert_called_once()

    def test_calendar_routing_requires_owner_and_enabled_connection(self):
        self.router.poll.side_effect = None
        self.router.poll.return_value = {"action": "calendar", "repository": "", "objective_id": "", "reply": ""}
        body = self.body("What is on my calendar tomorrow?")
        with patch("capo.calendar.CalendarConversation") as calendar:
            self.assertIn("not connected", self.service.dispatch("Cal1", body))
            calendar.assert_not_called()
            self.config["calendar"] = {"enabled": True, "timezone": "America/Los_Angeles"}
            calendar.return_value.poll.return_value = {"reply": "Your calendar is clear."}
            self.assertEqual(self.service.dispatch("Cal2", body), "Your calendar is clear.")
            context = calendar.return_value.poll.call_args.args[1]
            self.assertEqual(context["message"], "What is on my calendar tomorrow?")
            body["event"]["user"] = "UOTHER"
            with self.assertRaisesRegex(ValueError, "Unauthorized"):
                self.service.dispatch("Cal3", body)
            calendar.return_value.poll.assert_called_once()

    def test_browser_approval_requires_delivered_step_in_same_thread(self):
        from capo import browser, browser_slack
        from capo.conversation import _write
        self.config['browser'] = {'enabled': True, 'start_url': 'https://cinema.example/',
                                  'allowed_origins': ['https://cinema.example']}
        response = self.service.dispatch('EvBrowser', self.body('browse: Find a movie'))
        self.assertIn('browser', response)
        state = list(browser_slack.sessions(self.service))[0]
        state.update(status='awaiting_approval', pid=__import__('os').getpid(), message='Open the showtimes.',
                     pending={'digest': 'abc', 'expires': 10**12, 'effect': 'click: Showtimes'})
        directory = browser.location(self.store.home, state['id'])
        _write(directory/'state.json', state)
        self.assertIn('wait', self.service.dispatch('EvEarly', self.body('approve')))
        self.assertFalse((directory/'approval.json').exists())
        with patch.object(self.service, 'send_chunk', return_value=True):
            browser_slack.tick(self.service)
        other = self.body('approve'); other['event']['thread_ts'] = 'different'
        self.service.dispatch('EvOther', other)
        self.assertFalse((directory/'approval.json').exists())
        self.assertIn('Approved', self.service.dispatch('EvApproveBrowser', self.body('approve')))
        self.assertEqual(json.loads((directory/'approval.json').read_text())['digest'], 'abc')

    def test_plain_thread_replies_require_a_known_owner_conversation(self):
        reply = self.body('help', 'EvPlain')
        reply['event'].update(type='message', text='help', ts='124.0', thread_ts='123.456')
        self.assertFalse(ingest(self.store.home, self.config, reply))
        self.assertTrue(ingest(self.store.home, self.config, self.body('help', 'EvStart')))
        self.assertTrue(ingest(self.store.home, self.config, reply))
        self.assertEqual(self.service.dispatch('EvPlain', reply), HELP)
        stranger = self.body('help', 'EvStranger')
        stranger['event'].update(type='message', text='help', ts='125.0', thread_ts='123.456', user='UOTHER')
        self.assertFalse(ingest(self.store.home, self.config, stranger))
        top = self.body('help', 'EvTop')
        top['event'].update(type='message', text='help')
        self.assertFalse(ingest(self.store.home, self.config, top))
        changed = dict(reply, event=dict(reply['event'], subtype='message_changed'))
        self.assertFalse(ingest(self.store.home, self.config, changed))

    def test_duplicate_thread_mentions_are_processed_once(self):
        ingest(self.store.home, self.config, self.body('help', 'EvStart'))
        mention = self.body('help', 'EvMentionReply')
        mention['event'].update(ts='125.0', thread_ts='123.456')
        message = dict(mention, event_id='EvMessageReply', event=dict(mention['event'], type='message'))
        self.assertTrue(ingest(self.store.home, self.config, message))
        self.assertFalse(ingest(self.store.home, self.config, mention))
        count = self.store.db.execute('SELECT COUNT(*) FROM slack_inbox').fetchone()[0]
        self.assertEqual(count, 2)

    def test_superseded_blocker_is_not_announced(self):
        self.service.dispatch("Ev123", self.body())
        original = self.store.list()[0]
        original.update(status="blocked", error="Revision limit reached")
        self.store.save(original, "fixture")
        continuation = self.store.create(dict(original, continuation_of=original["id"]))
        self.service.process_notifications()
        self.assertEqual(self.client.messages, [])

    def test_routine_delivery_runs_before_completion_notice(self):
        self.service.dispatch("Ev123", self.body())
        objective = self.store.list()[0]
        objective["status"] = "completed"
        self.store.save(objective, "fixture")
        self.config["repositories"]["project"].update(allow_publication=True, auto_publish_routine=True)
        def deliver(store, identifier, settings):
            row = store.get(identifier)
            row["publication"] = {"status": "published", "pr": {"url": "https://github.com/owner/project/pull/1"}}
            store.save(row, "fixture_published")
        with patch("capo.delivery.deliver_routine", side_effect=deliver):
            self.service.tick()
        self.assertEqual(len(self.client.messages), 1)
        self.assertIn("/pull/1", self.client.messages[0]["text"])
        self.assertNotIn("prepare", self.client.messages[0]["text"])

    def test_routine_policy_requires_publication_permission(self):
        self.config["repositories"]["project"]["auto_publish_routine"] = True
        with self.assertRaises(ValueError):
            validate_config(self.config)

    def test_bold_copied_command_uses_direct_parser(self):
        body = self.body("help")
        body["event"]["text"] = "*<@UBOT> help*"
        self.assertIn("Mention Capo", self.service.dispatch("EvBold", body))
        self.router.poll.assert_not_called()

    def test_merged_pr_needs_no_new_preview_or_approval(self):
        self.service.dispatch("Ev123", self.body())
        objective = self.store.list()[0]
        objective.update(status="completed", publication={"status": "published"})
        self.store.save(objective, "fixture")
        self.config["repositories"]["project"]["allow_publication"] = True
        for command in ("prepare", "approve"):
            body = self.body(f"{command} {objective['id']}")
            with patch("capo.github.sync", return_value={"state": "MERGED", "url": "https://github.com/owner/project/pull/1"}), \
                    patch("capo.github.prepare") as prepare, patch("capo.github.publish") as publish:
                result = self.service.dispatch("EvMerged", body)
            self.assertIn("already been merged", result)
            self.assertIsNone(self.service.pending_review)
            prepare.assert_not_called()
            publish.assert_not_called()

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

    def test_blocked_notification_explains_planning_conflict_without_raw_errors(self):
        from capo.slack import blocked_reason
        self.service.dispatch("Ev123", self.body())
        objective = self.store.list()[0]
        objective.update(status="blocked", error="Implementation and explicit reviewer must use different providers")
        self.store.save(objective, "fixture")
        self.service.tick()
        self.assertIn("same provider", self.client.messages[-1]["text"])
        objective.update(error="secret-sentinel in private diagnostics", active_stage="reviewer")
        explanation = blocked_reason(objective)
        self.assertIn("independent review", explanation)
        self.assertNotIn("secret-sentinel", explanation)

    def test_legacy_natural_issue_question_reads_selected_repository_without_queuing(self):
        git(self.repo, "remote", "add", "origin", "https://github.com/example/project.git")
        self.router.poll.side_effect = None
        self.router.poll.return_value = {"action": "issues", "repository": "project", "objective_id": "", "reply": ""}
        body = self.body(", can you check if Project has any issues that need attention?")
        with patch("capo.github.GitHub.issues", return_value=[{"number": 7, "title": "Fix bug", "url": "https://github.com/example/project/issues/7", "labels": []}]) as issues:
            response = self.service.dispatch("Ev123", body)
        issues.assert_called_once_with("example/project")
        self.assertIn("#7: Fix bug", response)
        self.assertEqual(self.store.list(), [])
        self.assertFalse(self.router.poll.call_args.args[1]["message"].startswith(","))

    def test_legacy_natural_objective_uses_original_request_and_trusted_checks(self):
        self.router.poll.side_effect = None
        self.router.poll.return_value = {"action": "objective", "repository": "project", "objective_id": "", "reply": ""}
        request = "Please improve Project's greeting"
        self.service.dispatch("Ev123", self.body(request))
        self.service.dispatch("Ev123", self.body(request))
        self.assertEqual(len(self.store.list()), 1)
        objective = self.store.list()[0]
        self.assertEqual(objective["request"], request)
        self.assertEqual(objective["checks"], [["python3", "hello.py"]])

    def test_legacy_natural_status_in_thread_does_not_reopen_completed_objective(self):
        self.service.dispatch("Ev123", self.body())
        objective = self.store.list()[0]
        objective["status"] = "completed"
        self.store.save(objective, "fixture")
        self.router.poll.side_effect = None
        self.router.poll.return_value = {"action": "status", "repository": "", "objective_id": objective["id"], "reply": ""}
        body = self.body("How is it going?", "Ev456")
        body["event"]["thread_ts"] = "123.456"
        self.assertIn("completed", self.service.dispatch("Ev456", body))
        self.assertEqual(self.store.followups(objective["id"]), [])

    def test_work_updates_send_one_plan_and_no_stage_chatter_across_restart(self):
        from unittest.mock import Mock
        self.service.dispatch("Ev123", self.body())
        objective = self.store.list()[0]
        objective.update(status="running", plan={"summary": "Update the greeting and check its output."})
        self.store.save(objective, "fixture")
        self.service.active = Mock()
        self.service.active.poll.return_value = None
        self.service.active_id = objective["id"]
        for stage in ["implementer", "verification", "reviewer", "acceptance", "implementer"]:
            objective["active_stage"] = stage
            self.store.save(objective, "fixture")
            self.service.tick()
        self.assertEqual(len(self.client.messages), 1)
        self.assertIn("Update the greeting", self.client.messages[0]["text"])
        restarted = SlackService(self.store, self.config, self.client)
        restarted.tick()
        self.assertEqual(len(self.client.messages), 1)

    def test_thread_followup_targets_explicit_continuation(self):
        self.service.dispatch("Ev123", self.body())
        original = self.store.list()[0]
        original["status"] = "blocked"
        self.store.save(original, "fixture")
        continuation = self.store.create(dict(original, continuation_of=original["id"]))
        body = self.body("followup: Preserve compatibility", "Ev456")
        body["event"]["thread_ts"] = "123.456"
        self.service.dispatch("Ev456", body)
        self.assertEqual(self.store.followups(original["id"]), [])
        self.assertEqual(len(self.store.followups(continuation["id"])), 1)

    def test_native_activity_refresh_clear_and_failure_do_not_block_reply(self):
        from unittest.mock import Mock
        from capo.conversation import ConversationPending
        self.client.assistant_threads_setStatus = Mock(return_value={'ok':True})
        self.router.poll.side_effect = ConversationPending()
        body=self.body('Please investigate', 'activity')
        ingest(self.store.home,self.config,body)
        with patch('capo.slack.time.monotonic',return_value=100):
            self.service.process_messages();self.service.process_messages()
        self.client.assistant_threads_setStatus.assert_called_once()
        self.assertEqual(self.client.assistant_threads_setStatus.call_args.kwargs['thread_ts'],'123.456')
        with patch('capo.slack.time.monotonic',return_value=161):
            self.service.process_messages()
        self.assertEqual(self.client.assistant_threads_setStatus.call_count,2)
        self.router.poll.side_effect=None
        self.router.poll.return_value={'action':'reply','repository':'','objective_id':'','reply':'Done.'}
        self.client.assistant_threads_setStatus.side_effect=RuntimeError('Unavailable')
        self.service.process_messages()
        self.assertEqual(self.client.messages[-1]['text'],'Done.')
        self.assertEqual(self.client.assistant_threads_setStatus.call_args.kwargs['status'],'')
        self.assertEqual(list(self.store.pending_slack()),[])

    def test_native_activity_not_shown_for_unauthorized_messages(self):
        from unittest.mock import Mock
        self.client.assistant_threads_setStatus=Mock()
        body=self.body('Hello','unauthorized');body['event']['user']='UOTHER'
        self.store.enqueue_slack('unauthorized',body)
        self.service.process_messages()
        self.client.assistant_threads_setStatus.assert_not_called()

    def test_pending_conversation_does_not_block_other_commands(self):
        from capo.conversation import ConversationPending
        self.router.poll.side_effect = ConversationPending()
        ingest(self.store.home, self.config, self.body("What's happening?", "Ev123"))
        ingest(self.store.home, self.config, self.body("help", "Ev456"))
        self.service.tick()
        self.assertEqual([row[0] for row in self.store.pending_slack()], ["Ev123"])
        self.assertEqual(len(self.client.messages), 1)

    def test_conversation_cannot_publish_or_cross_current_policy(self):
        self.router.poll.side_effect = None
        for route in [
            {"action": "approve", "repository": "project", "objective_id": "", "reply": ""},
            {"action": "issues", "repository": "not_configured", "objective_id": "", "reply": ""}]:
            self.router.poll.return_value = route
            with self.assertRaises(ValueError):
                self.service.dispatch("Ev123", self.body("Do it"))
        body = self.body("List project issues")
        body["event"]["user"] = "UOTHER"
        self.router.poll.reset_mock()
        with self.assertRaisesRegex(ValueError, "Unauthorized"):
            self.service.dispatch("Ev123", body)
        self.router.poll.assert_not_called()

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

    def test_capacity_wait_does_not_busy_loop_runner(self):
        import time
        self.service.dispatch('Ev123', self.body())
        objective = self.store.list()[0]
        objective['capacity_retry_at'] = time.time() + 3600
        self.store.save(objective, 'capacity_waiting')
        self.config['auto_run'] = True
        with patch('capo.slack.subprocess.Popen') as process:
            self.service.tick()
            process.assert_not_called()
            objective['capacity_retry_at'] = 0
            self.store.save(objective, 'quota_reset')
            self.service.tick()
            process.assert_called_once()

    def test_exited_runner_with_capacity_checkpoint_remains_queued(self):
        import time
        from unittest.mock import Mock
        self.service.dispatch('Ev123', self.body())
        objective = self.store.list()[0]
        objective.update(capacity_retry_at=time.time()+3600, supervisor_pid=12345)
        self.store.save(objective, 'capacity_waiting')
        self.service.active = Mock(pid=12345)
        self.service.active.poll.return_value = 0
        self.service.active_id = objective['id']
        self.service.tick()
        self.assertEqual(self.store.get(objective['id'])['status'], 'queued')
        self.assertIsNone(self.service.active)

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
        self.assertIn("passed its checks", self.client.messages[-1]["text"])

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
        with patch("capo.github.prepare", return_value=publication):
            self.service.dispatch("EvDetailsFirst", self.body(f"details {identifier}"))
        self.assertIsNone(self.service.pending_review)
        self.assertNotIn("slack_review_digest", self.store.get(identifier))
        ingest(self.store.home, self.config, self.body("prepare " + identifier, "EvPrepare"))
        with patch("capo.github.prepare", return_value=publication):
            self.service.process_messages()
        self.assertNotIn("+hello world", self.client.messages[-1]["text"])
        self.assertLess(len(self.client.messages[-1]["text"]), 650)
        self.assertIn("does not merge", self.client.messages[-1]["text"])
        self.assertIn("example/project", self.client.messages[-1]["text"])
        with patch("capo.github.prepare", return_value=publication):
            details = self.service.dispatch("EvDetails", self.body(f"details {identifier}"))
        self.assertIn("+hello world", details)
        self.assertIn("One check passed.", details)
        self.assertEqual(self.store.get(identifier)["slack_review_digest"], digest)
        with patch("capo.github.publish", return_value={"pr": {"url": "https://github.com/example/project/pull/1"}}) as publish:
            self.service.dispatch("EvApprove", self.body(f"approve {identifier} {digest}"))
            self.assertEqual(publish.call_args.args[2], digest)
            self.service.dispatch("EvShort", self.body(f"approve {identifier}"))
            self.assertEqual(publish.call_args.args[2], digest)
            plain = self.body("approve")
            plain["event"].update(type="message", text="approve", thread_ts="123.456", ts="126.0")
            self.service.dispatch("EvBare", plain)
            self.assertEqual(publish.call_args.args[2], digest)
            publish.reset_mock()
            early = self.body(f"approve {identifier}")
            early["event"].update(ts="122.0", thread_ts="123.456")
            self.assertIn("prepare", self.service.dispatch("EvEarly", early))
            publish.assert_not_called()
            other = self.body(f"approve {identifier}")
            other["event"]["thread_ts"] = "999.1"
            self.assertIn("prepare", self.service.dispatch("EvWrongThread", other))
            publish.assert_not_called()
            changed = self.store.get(identifier)
            changed["publication"]["digest"] = "f" * 64
            self.store.save(changed, "changed_preview")
            self.assertIn("prepare", self.service.dispatch("EvStaleShort", self.body(f"approve {identifier}")))
            publish.assert_not_called()

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

    def test_complete_document_delivery_survives_restart_between_chunks(self):
        from capo.research_tools import complete_reply
        content = 'Use concrete verbs and keep the necessary details.\n' * 220
        text = complete_reply({'reply': 'Here is your guide.',
                               'document_title': 'Writing guide', 'document': content})
        ingest(self.store.home, self.config, self.body('help', 'EvDocument'))
        with patch.object(self.service, 'dispatch', return_value=text) as dispatch:
            self.service.process_messages()
            dispatch.assert_called_once()
        restarted = SlackService(self.store, self.config, self.client)
        with patch.object(restarted, 'dispatch', side_effect=AssertionError('Already generated')):
            import time
            now = time.time()
            for index in range(10):
                with patch('capo.slack.time.time', return_value=now + 10 * (index + 1)):
                    restarted.process_messages()
        delivered = ''.join(row['text'] for row in self.client.messages)
        self.assertEqual(delivered, text)
        self.assertIn(content.strip(), delivered)
        self.assertFalse(self.store.pending_slack())

    def test_formatting_is_saved_before_chunks_and_preserved_across_restart(self):
        import html
        from capo.message_format import plain_text
        text = '**Guide**\n' + 'Keep all details.\n' * 200 + '```xml\n</invoke>\n```\n</document>\n</invoke>'
        ingest(self.store.home, self.config, self.body('help', 'EvFormat'))
        with patch.object(self.service, 'dispatch', return_value=text):
            self.service.process_messages()
        restarted = SlackService(self.store, self.config, self.client)
        with patch.object(restarted, 'dispatch', side_effect=AssertionError('Already generated')):
            import time
            now = time.time()
            for index in range(10):
                with patch('capo.slack.time.time', return_value=now + 10 * (index + 1)):
                    restarted.process_messages()
        self.assertEqual(''.join(row['text'] for row in self.client.messages), html.escape(plain_text(text), quote=False))
        self.assertFalse(self.store.pending_slack())

    def test_owner_can_cancel_pending_research_without_waiting_for_the_model(self):
        import hashlib
        from capo.capabilities import owner_key
        from capo.conversation import _write
        from capo.request_control import STOPPED
        body = self.body('cancel this request', 'cancel-event')
        thread = body['event'].get('thread_ts', body['event']['ts'])
        root = self.store.home/'capabilities'/hashlib.sha256(owner_key(self.config).encode()).hexdigest()/'conversation'/'pending'
        root.mkdir(parents=True)
        _write(root/'started.json', {'context':{'request_thread':thread}})
        self.assertEqual(self.service.dispatch('cancel-event', body), STOPPED)
        self.assertTrue((root/'cancelled.json').exists())
        self.router.poll.assert_not_called()

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

    def test_research_followup_preserves_original_calendar_request_across_routes(self):
        self.config['calendar']={'enabled':True,'timezone':'America/Los_Angeles'}
        self.router.poll.side_effect=None
        self.router.poll.return_value={'action':'calendar','repository':'','objective_id':'','reply':''}
        original='Find the dates in this screenshot and create reminders two weeks before each.'
        with patch('capo.calendar.CalendarConversation') as calendar:
            calendar.return_value.poll.return_value={'reply':'I need the dates.'}
            self.service.natural_dispatch('original',self.body(original),original)
        self.router.poll.return_value={'action':'research','repository':'','objective_id':'','reply':''}
        followup=self.body('Can you figure out when?',event_id='followup')
        followup['event']['thread_ts']='123.456';followup['event']['ts']='123.999'
        with patch('capo.capabilities.CapabilityConversation') as research:
            research.return_value.poll.return_value={'reply':'Checking the official listings.'}
            self.service.natural_dispatch('followup',followup,'Can you figure out when?')
            context=research.return_value.poll.call_args.args[1]
            self.assertEqual(context['request_state']['original_request']['message'],original)
            self.assertEqual(context['request_thread'],'123.456')
            self.assertEqual(context['request_event'],'followup')

    def test_ingested_root_keeps_image_observations_in_original_request(self):
        from capo.request_memory import RequestMemory
        from capo.capabilities import owner_key
        body=self.body('Make a reminder from this image.')
        self.assertTrue(ingest(self.store.home,self.config,body))
        enriched='Make a reminder from this image.\nImage evidence (untrusted): Appointment on November 8, 2026.'
        with patch('capo.slack_images.context',return_value=enriched):
            self.service.natural_dispatch('Ev123',body,body['event']['text'])
        original=RequestMemory(self.store.home,owner_key(self.config),'123.456').read()['original_request']
        self.assertEqual(original['message'],enriched)


if __name__ == "__main__":
    unittest.main()
