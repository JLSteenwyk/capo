import base64
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from capo.capabilities import Documents, owner_key, shared_tools
from capo.monitoring import Monitor
from capo.observations import Observations
from capo.research_tools import ReadTools, ToolInputError
from test_tasks import fields

CONFIG = {'team_id': 'T1', 'channel_id': 'C1', 'owner_user_id': 'U1', 'gmail': {'enabled': True}}
BODY = {'payload': {'mimeType': 'text/plain', 'body': {'data': base64.urlsafe_b64encode(b'Files attached.').decode()}}}


class ObservedMailReadTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)

    def registry(self, request=None):
        return shared_tools(self.home, CONFIG, Documents(self.home, owner_key(CONFIG)), request)

    def test_observed_email_is_readable_without_a_redundant_search(self):
        request = {'observations': [{'kind': 'email', 'id': 'email:obs', 'message_id': 'obs', 'title': 'Files'},
                                    {'kind': 'github', 'id': 'gh', 'url': 'https://example.org/issue'}]}
        with patch('capo.gmail.Gmail') as gmail:
            gmail.return_value.get.return_value = BODY
            tools = self.registry(request)
            read = tools.call('mail.read', {'ids': ['obs'], 'strip_quotes': False})
            self.assertEqual(read['messages'][0]['body'], 'Files attached.')
            # Composes with metadata triage on the same supplied source.
            gmail.return_value.get.return_value = {'payload': {'headers': [{'name': 'Subject', 'value': 'Files'}]}}
            self.assertEqual(tools.call('mail.metadata', {'ids': ['obs']})['messages'][0]['headers']['subject'], 'Files')

    def test_unknown_ids_are_rejected_with_a_correctable_reason_and_no_read(self):
        with patch('capo.gmail.Gmail') as gmail:
            tools = self.registry({'observations': [{'kind': 'email', 'message_id': 'obs'}]})
            for name, arguments in (('mail.read', {'ids': ['other'], 'strip_quotes': True}), ('mail.metadata', {'ids': ['other']})):
                with self.assertRaises(ToolInputError) as caught:
                    tools.call(name, arguments)
                self.assertIn('Unknown message IDs: other', str(caught.exception))
                self.assertIn('mail.search', str(caught.exception))
            with self.assertRaises(ToolInputError):
                tools.call('mail.thread', {'id': 'thread'})
            gmail.return_value.get.assert_not_called()
        # Without supplied observations, nothing becomes readable implicitly.
        with patch('capo.gmail.Gmail'), self.assertRaises(ToolInputError):
            self.registry().call('mail.read', {'ids': ['obs'], 'strip_quotes': True})


class ClosedTaskObservationTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.store = Observations(Path(tmp.name), owner_key({}))
        self.batch = self.store.changed([{'id': 'v1', 'kind': 'email', 'message_id': 'm', 'title': 'Availability request'}])
        self.tools = ReadTools(self.store.tools(self.batch))

    def observe(self, id, revision, status, notes, op):
        return self.tools.call('commitments.observe', {'id': id, 'expected_revision': revision,
            'fields': fields(status=status, notes=notes, sources=['email:m']), 'evidence_refs': ['email:m'],
            'certainty': 'explicit'}, operation_id=op)

    def test_repeating_a_completion_is_a_no_op_but_reopening_is_still_refused(self):
        task = self.observe('', '', 'open', 'Asked to share availability.', 'create')['task']
        done = self.observe(task['id'], str(task['revision']), 'completed', 'Reply confirms availability was shared.', 'complete')['task']
        again = self.observe(done['id'], str(done['revision']), 'completed', 'Reworded: availability was shared.', 'repeat')
        self.assertFalse(again['changed'])
        stored = self.store.tasks.get(done['id'])
        self.assertEqual((again['task']['revision'], stored['revision'], stored['notes']),
                         (done['revision'], done['revision'], 'Reply confirms availability was shared.'))
        with self.assertRaises(PermissionError):
            self.observe(done['id'], str(done['revision']), 'open', 'Reopen it.', 'reopen')


class CommitmentAlertTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name); self.now = datetime(2030, 1, 3, 18, tzinfo=timezone.utc)
        self.item = {'id': 'v1', 'kind': 'email', 'message_id': 'm', 'title': 'Fw: meeting files'}

    def test_first_failure_is_quiet_and_exhausted_retry_names_the_source(self):
        monitor = Monitor(self.home, {})
        with patch('capo.monitoring.research', return_value={'status': 'partial'}):
            monitor.tick(self.now, [self.item])
        self.assertEqual(monitor.notices(), [])
        with patch('capo.monitoring.research', return_value={'status': 'partial'}), \
             patch('capo.task_evidence.TaskEvidence.source', return_value={'status': 'checked', 'messages': []}):
            monitor.tick(self.now+timedelta(hours=2), [])
        [notice] = monitor.notices()
        self.assertIn('“Fw: meeting files” after 2 attempts', notice['summary'])
        self.assertIn('Check it yourself', notice['summary'])

    def test_redundant_update_of_a_task_completed_in_the_same_review_finishes_it(self):
        def review(provider, tools, request, directory, **kwargs):
            ref = request['observations'][0]['source_reference']
            def observe(id, revision, notes, op):
                return tools.call('commitments.observe', {'id': id, 'expected_revision': revision,
                    'fields': fields(status='completed' if id else 'open', notes=notes, sources=[ref]),
                    'evidence_refs': [ref], 'certainty': 'explicit'}, operation_id=op)['task']
            task = observe('', '', 'Asked for files.', 'one')
            done = observe(task['id'], str(task['revision']), 'Files were sent.', 'two')
            observe(done['id'], str(done['revision']), 'Files were sent (reworded).', 'three')
            return {'status': 'reported_complete'}
        monitor = Monitor(self.home, {})
        with patch('capo.monitoring.research', side_effect=review):
            monitor.tick(self.now, [self.item])
        self.assertEqual(monitor.notices(), [])
        self.assertEqual(monitor.observations.changed([self.item]), [])


if __name__ == '__main__':
    unittest.main()
