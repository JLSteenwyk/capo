import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from capo.assignment_reports import AssignmentReport
from capo.contracts import object_schema
from capo.gmail import GmailReadTools
from capo.monitoring_progress import continuation, incomplete_report
from capo.research_tools import ReadTool, ReadTools, research


def step(name, args):
    return dict(action='tool', tool=name, arguments_json=json.dumps(args), reply='', document='', document_title='')


def done():
    return dict(action='finish', tool='', arguments_json='{}', reply='Partial findings saved.', document='', document_title='')


class MonitoringLimitsTests(unittest.TestCase):
    def test_reporting_slots_are_enforced_for_nonmail_tools(self):
        read = Mock(return_value={'source': 'synthetic'})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            report = AssignmentReport(path)
            tools = ReadTools([ReadTool('files.read', 'Read', object_schema({}), read), report.tool()])
            provider = Mock()
            provider.call.side_effect = [step('files.read', {}), step('files.read', {}),
                step('monitor.report', dict(findings=[], blockers=['One source remains unchecked.'], coverage='One file read.')), done()]
            result = research(provider, tools, {}, path, max_calls=3)
            self.assertEqual(read.call_count, 1)
            self.assertEqual(report.read()['coverage'], 'One file read.')
            self.assertIn('unavailable', result['receipts'][1]['error'])
            self.assertIn('"report_required": true', provider.call.call_args_list[1].args[1])

    def test_exhaustion_blocks_repeated_reads_and_survives_restart(self):
        client = Mock()
        client.get.return_value = {'payload': {'mimeType': 'text/plain', 'body': {'data': base64.urlsafe_b64encode(b'x'*20).decode()}}}
        mail = GmailReadTools(client)
        mail.known_ids.update(['a', 'b'])
        mail.characters = 119990
        provider = Mock()
        provider.call.side_effect = [step('mail.read', dict(ids=['a'], strip_quotes=False)),
                                    step('mail.read', dict(ids=['b'], strip_quotes=False)), done()]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            result = research(provider, mail, {}, path, max_calls=3)
            saved = json.loads((path/'checkpoint.json').read_text())
            resumed = GmailReadTools(client)
            resumed.restore(saved['tool_state'])
            self.assertIn('mail.read', resumed.unavailable())
            self.assertEqual(resumed.characters, 120000)
            research(Mock(), resumed, {}, path, max_calls=3)
            self.assertEqual(resumed.characters, 120000)
            self.assertEqual(client.get.call_count, 1)
            self.assertIn('unavailable', result['receipts'][1]['error'])

    def test_next_run_resumes_unread_ids_and_same_query_cursor(self):
        client = Mock()
        client.get.side_effect = [{'messages': [{'id':'a'}, {'id':'b'}], 'nextPageToken':'next'},
                                 {'payload':{}}, {'messages':[{'id':'c'}]}]
        mail = GmailReadTools(client)
        mail.search('subject:invoice', '2', '')
        mail.read(['a'], False)
        progress = continuation(mail)
        self.assertNotIn('body', json.dumps(progress))
        new = GmailReadTools(client)
        new.restore(progress['state'], continuation=True)
        self.assertEqual(new.known_ids, {'b'})
        self.assertEqual(new.read_attempts, 0)
        with self.assertRaises(ValueError):
            new.search('different query', '2', 'next')
        new.search('subject:invoice', '2', 'next')
        self.assertEqual(new.known_ids, {'b','c'})
        # Consuming a page does not lose unread IDs from the preceding page.
        next_state = new.next_scan_state()
        self.assertEqual(next_state['known_ids'], ['b','c'])

    def test_complete_scan_has_no_continuation_and_fallback_is_honest(self):
        mail = GmailReadTools(Mock())
        mail.known_ids = {'a'}
        mail.inspected_ids = {'a'}
        self.assertEqual(continuation(mail)['state'], {})
        fallback = incomplete_report([{'tool':'mail.read', 'result':{'messages':[{'id':'a'}]}},
                                      {'tool':'mail.read', 'error':'failed'}])
        self.assertEqual(fallback['findings'], [])
        self.assertIn('1 email excerpts', fallback['coverage'])
        self.assertIn('not a clean check', fallback['coverage'])

    def test_report_can_be_saved_when_source_evidence_budget_is_full(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            report = AssignmentReport(path)
            tools = ReadTools([ReadTool('files.read', 'Read', object_schema({}),
                                       lambda: {'text': 'x' * 21000}), report.tool()])
            provider = Mock()
            provider.call.side_effect = [step('files.read', {}),
                step('monitor.report', dict(findings=[], blockers=['Source exceeded evidence allowance.'], coverage='Incomplete.')), done()]
            result = research(provider, tools, {}, path, max_calls=4,
                              limits={'max_calls':4, 'evidence_chars':20000})
            self.assertTrue(result['receipts'][0]['result']['truncated'])
            self.assertTrue(result['receipts'][1]['result']['saved'])
            self.assertLessEqual(json.loads((path/'checkpoint.json').read_text())['evidence_chars_used'], 20000)
            self.assertTrue((path/'evidence-0.json').exists())

    def test_scheduler_carries_unread_work_to_next_occurrence(self):
        from datetime import datetime, timedelta
        from types import SimpleNamespace
        from unittest.mock import patch
        from capo.capabilities import owner_key
        from capo.schedules import Schedules
        from capo.scheduled_requests import ScheduledManager
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config = dict(team_id='T', channel_id='C', owner_user_id='U')
            Schedules(home, owner_key(config)).save('', '', dict(title='Document check',
                request='Inspect matching sources.', weekdays=list('0123456'), time='10:00',
                timezone='America/Los_Angeles', enabled=True, catch_up_hours='1',
                agent='capo', delivery='changes'), 'save')
            client = Mock()
            client.chat_postMessage.return_value = {'ts':'synthetic'}
            service = SimpleNamespace(store=SimpleNamespace(home=home), config=config, client=client, bot_user_id='BOT')
            registries = []
            def registry(*args):
                mail = GmailReadTools(Mock())
                registries.append(mail)
                return mail
            def generate(provider, tools, request, directory, **kwargs):
                mail = registries[-1]
                if len(registries) == 1:
                    mail.known_ids.update(['read', 'unread'])
                    mail.inspected_ids.add('read')
                    mail.characters = 120000
                else:
                    self.assertEqual(mail.known_ids, {'unread'})
                    self.assertEqual(mail.characters, 0)
                    self.assertIn('continuation', request['previous_check'])
                return dict(reply='Incomplete.', document='', document_title='', status='partial',
                            receipts=[dict(tool='mail.read', result={'messages':[{'id':'read'}]})])
            start = datetime.fromisoformat('2030-01-01T10:00:00-08:00')
            with patch('capo.scheduled_requests.shared_tools', side_effect=registry), patch('capo.scheduled_requests.research', side_effect=generate):
                for day in range(2):
                    manager = ScheduledManager(service)
                    try:
                        now = start + timedelta(days=day)
                        manager.tick(now)
                        for worker in manager.workers.values():
                            worker.join(5)
                            self.assertFalse(worker.is_alive())
                        manager.tick(now + timedelta(seconds=30))
                    finally:
                        manager.db.close()
            self.assertEqual(len(registries), 2)
