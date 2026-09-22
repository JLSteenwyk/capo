import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch,Mock

from restart_fixture import Remote,PLAN,DRAFT
from capo.calendar_actions import CalendarActions
from capo.drafts import DraftTools
from capo.gmail import GmailReadTools
from capo.slack_outbox import SlackOutbox,pending


class RestartBoundaryTests(unittest.TestCase):
    def kill(self,root,kind):
        result=subprocess.run([sys.executable,str(Path(__file__).with_name('restart_fixture.py')),str(root),kind],
                              stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=15)
        self.assertEqual(result.returncode,73,result.stderr.decode())
        self.assertEqual(Remote(root).state()['writes'],1)

    def test_process_death_after_calendar_create_update_and_delete_never_replays(self):
        for action in ('create','update','delete'):
            with self.subTest(action=action),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);self.kill(root,'calendar-'+action);remote=Remote(root)
                with patch('capo.calendar_actions.GoogleCalendar',return_value=remote):
                    restored=CalendarActions(root/'capo','owner','America/Los_Angeles',{})
                    item=restored.pending()['actions'][0]
                    result=restored.reconcile(item['id'])
                    self.assertTrue(result['verified'])
                    self.assertEqual(restored.pending()['actions'],[])
                    self.assertEqual(remote.state()['writes'],1)
                    events=remote.events()
                    self.assertEqual(len(events),0 if action=='delete' else 1)
                    if action=='update':self.assertEqual(events[0]['description'],'Preserve these notes.')

    def test_process_death_after_draft_save_rebuilds_all_adapters_without_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.kill(root,'draft');remote=Remote(root)
            restored=DraftTools(remote,GmailReadTools(remote),root/'capo','owner')
            action=restored.pending()['actions'][0]['id']
            self.assertTrue(restored.reconcile(action)['verified'])
            self.assertTrue(restored.save(**DRAFT,operation_id='original')['saved'])
            self.assertEqual(remote.state()['writes'],1)
            self.assertEqual(len(remote.state()['drafts']),1)

    def test_process_death_after_slack_acceptance_reconciles_without_resend(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.kill(root,'slack');remote=Remote(root)
            outbox=SlackOutbox(root/'capo',remote,'synthetic-bot')
            self.assertEqual(len(pending(root/'capo')),1)
            payload={'channel':'synthetic-channel','thread_ts':'1.000','text':'All requested details.'}
            self.assertTrue(outbox.post('reply-one',payload))
            self.assertTrue(outbox.post('reply-one',payload))
            self.assertEqual(remote.state()['writes'],1)
            self.assertEqual(pending(root/'capo'),[])

    def test_unknown_slack_result_or_inaccessible_history_never_repeats(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp);client=Mock();client.chat_postMessage.side_effect=TimeoutError()
            payload={'channel':'synthetic-channel','thread_ts':'1.000','text':'Answer'}
            outbox=SlackOutbox(home,client,'synthetic-bot')
            self.assertFalse(outbox.post('reply',payload))
            client.conversations_replies.side_effect=PermissionError()
            self.assertFalse(SlackOutbox(home,client,'synthetic-bot').post('reply',payload))
            self.assertEqual(client.chat_postMessage.call_count,1)
            self.assertEqual(len(pending(home)),1)

    def test_spoofed_or_changed_slack_message_does_not_confirm_delivery(self):
        for changed in ({'user':'someone-else'},{'text':'Different content'},{'thread_ts':'other'}):
            with self.subTest(changed=changed),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);self.kill(root,'slack');remote=Remote(root)
                state=remote.state();state['messages'][0].update(changed)
                from capo.conversation import _write
                _write(remote.path,state)
                outbox=SlackOutbox(root/'capo',remote,'synthetic-bot')
                self.assertFalse(outbox.post('reply-one',{'channel':'synthetic-channel','thread_ts':'1.000','text':'All requested details.'}))
                self.assertEqual(remote.state()['writes'],1)
