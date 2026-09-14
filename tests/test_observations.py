import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from capo.capabilities import owner_key
from capo.delegation import authority
from capo.monitoring import Monitor, inbox
from capo.observations import Observations
from capo.research_tools import ReadTools
from test_tasks import fields


class ObservationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.store = Observations(self.home, owner_key({}))
        self.now = datetime(2030, 1, 3, 18, tzinfo=timezone.utc)
        self.email = {'id':'email-v1','kind':'email','message_id':'message-a','title':'Proposal due Friday'}
        self.github = {'id':'github-v1','kind':'github','url':'https://github.com/example/project/issues/1','title':'Proposal review moved to Monday'}

    def test_unchanged_sources_and_late_acknowledgement(self):
        first = self.store.changed([self.email])
        newer = dict(self.email, id='email-v2', title='Changed deadline')
        second = self.store.changed([newer])
        self.store.acknowledge(first)
        self.assertEqual(self.store.changed([newer]), second)
        self.store.acknowledge(second)
        self.assertEqual(Observations(self.home, owner_key({})).changed([newer]), [])
        self.assertEqual(len(Observations(self.home, 'other').changed([newer])), 1)

    def test_cross_integration_commitment_updates_keep_identity_and_no_action_grant(self):
        first = self.store.changed([self.email])
        tools = ReadTools(self.store.tools(first))
        task = tools.call('commitments.observe', {'id':'', 'expected_revision':'',
            'fields':fields(notes='Owner committed to review the proposal.',sources=['email:message-a']),
            'evidence_refs':['email:message-a'], 'certainty':'explicit'}, operation_id='observe')['task']
        second = self.store.changed([self.github])
        ref = second[0]['source_reference']
        updated = ReadTools(self.store.tools(second)).call('commitments.observe', {
            'id':task['id'], 'expected_revision':'1', 'fields':fields(notes='The project update moves the review to Monday.',
                sources=['email:message-a',ref], status='waiting',waiting_on='Updated proposal'),
            'evidence_refs':[ref], 'certainty':'explicit'}, operation_id='update')['task']
        self.assertEqual(updated['id'], task['id'])
        self.assertEqual(len(updated['sources']), 2)
        self.assertEqual(len(self.store.tasks.search()['tasks']), 1)
        self.assertIsNone(authority(self.store.tasks, task['id']))

    def test_inferences_cannot_become_obligations_or_reopen_dismissed_items(self):
        changed = self.store.changed([self.email])
        tools = ReadTools(self.store.tools(changed))
        args = {'id':'', 'expected_revision':'', 'fields':fields(notes='Maybe review this.',sources=['email:message-a']),
                'evidence_refs':['email:message-a'], 'certainty':'inferred'}
        with self.assertRaises(ValueError):
            tools.call('commitments.observe', args, operation_id='bad')
        args['fields']['status']='candidate'
        task = tools.call('commitments.observe', args, operation_id='candidate')['task']
        self.assertEqual(self.store.tasks.search()['tasks'], [])
        self.store.tasks.save(task['id'], '1', fields(status='dismissed'), 'dismiss')
        args.update(id=task['id'], expected_revision='2')
        with self.assertRaises(PermissionError):
            tools.call('commitments.observe', args, operation_id='reopen')
        args['evidence_refs']=['invented-proof']
        with self.assertRaises(ValueError):
            tools.call('commitments.observe', args, operation_id='invented')
        self.assertNotIn('tasks.delegate', tools.tools)
        args.update(id='', expected_revision='')
        args['evidence_refs']=['email:message-a']
        again = tools.call('commitments.observe', args, operation_id='rediscover')['task']
        self.assertEqual(again['id'], task['id'])
        self.assertEqual(again['status'], 'dismissed')

    def test_monitor_skips_unchanged_information_and_cannot_write_external_services(self):
        result = {'reply':'Nothing to track.', 'document':'', 'document_title':'', 'receipts':[]}
        def review(provider, tools, request, *args, **kwargs):
            writes = [t.name for t in tools.tools.values() if t.mutates]
            self.assertEqual(writes, ['commitments.observe'])
            self.assertNotIn('tasks.delegate', tools.tools)
            return result
        with patch('capo.monitoring.research', side_effect=review) as run:
            Monitor(self.home, {}).tick(self.now, [self.email])
            Monitor(self.home, {}).tick(self.now, [self.email])
            self.assertEqual(run.call_count, 1)

    def test_inbox_membership_refreshes_without_rereading_unchanged_messages(self):
        client = Mock()
        client.get.side_effect = [{'messages':[{'id':'a'}]},
            {'payload':{'headers':[{'name':'Subject','value':'Deadline'}]},'snippet':'Details'},
            {'messages':[{'id':'a'}]}, {'messages':[]}]
        self.assertEqual(inbox(client, self.store)['messages'][0]['subject'], 'Deadline')
        self.assertEqual(inbox(client, self.store)['messages'][0]['subject'], 'Deadline')
        self.assertEqual(inbox(client, self.store)['messages'], [])
        reads = [call for call in client.get.call_args_list if call.args[0].startswith('messages/')]
        self.assertEqual(len(reads), 1)

    def test_quiet_assessment_skips_repeat_calls_but_rechecks_approaching_events(self):
        from datetime import timedelta
        from capo.heartbeat import select
        provider = Mock()
        provider.call.return_value = {'alerts':[]}
        item = {'id':'meeting', 'kind':'calendar', 'title':'Planning',
                'start':{'dateTime':(self.now+timedelta(hours=6)).isoformat()}}
        first = select([item], {}, self.now, self.home, provider, self.store)
        self.assertEqual(first['text'], '')
        select([item], {}, self.now+timedelta(hours=1), self.home, provider, self.store)
        self.assertEqual(provider.call.call_count, 1)
        select([item], {}, self.now+timedelta(hours=5), self.home, provider, self.store)
        self.assertEqual(provider.call.call_count, 2)

    def test_selected_alert_is_not_suppressed_before_delivery(self):
        from capo.heartbeat import select
        provider = Mock()
        provider.call.return_value = {'alerts':[{'id':'a','reason':'A reply is due.'}]}
        item = {'id':'a','title':'A commitment'}
        select([item], {}, self.now, self.home, provider, self.store)
        select([item], {}, self.now, self.home, provider, self.store)
        self.assertEqual(provider.call.call_count, 2)
