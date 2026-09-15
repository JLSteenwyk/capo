import tempfile
import json
import unittest
from pathlib import Path
from unittest.mock import patch,Mock

from capo.capabilities import Documents, shared_tools, owner_key
from capo.repository import git
from capo.store import Store
from capo.workflow_tools import WorkflowTools


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.home = root/'state'
        repo = root/'repo'
        repo.mkdir()
        git(repo, 'init', '-b', 'main')
        git(repo, 'config', 'user.name', 'Fixture')
        git(repo, 'config', 'user.email', 'fixture@example.invalid')
        (repo/'README.md').write_text('Fixture\n')
        git(repo, 'add', '.')
        git(repo, 'commit', '-m', 'Fixture')
        self.config = {'team_id': 'T123', 'channel_id': 'C123', 'owner_user_id': 'U123',
            'auto_run': False, 'repositories': {'project': {'path': str(repo),
                'checks': ['python3 -c "print(1)"'], 'workers': ['codex'], 'reviewer': 'claude'}}}
        self.store = Store(self.home)
        self.addCleanup(self.store.db.close)
        self.body = {'team_id': 'T123', 'event_id': 'owner-event', 'event': {'type': 'app_mention',
            'channel': 'C123', 'user': 'U123', 'ts': '123.456', 'text': '<@UBOT> Improve the project documentation'}}
        self.store.enqueue_slack('owner-event', self.body)
        self.request = {'request_event': 'owner-event', 'request_thread': '123.456',
                        'owner_request': {'event': 'owner-event', 'text': 'not trusted for authentication'}}
        self.tools = WorkflowTools(self.home, self.config, self.request)

    def test_queue_inspect_and_cancel_reuse_existing_workflow(self):
        result = self.tools.start('project', 'Clarify the setup steps', False, 'operation')
        again = self.tools.start('project', 'Clarify the setup steps', False, 'operation')
        self.assertEqual(result, again)
        self.assertFalse(result['completed'])
        self.assertEqual(len(self.store.list()), 1)
        row = self.store.get(result['objective_id'])
        self.assertEqual(row['checks'], [['python3', '-c', 'print(1)']])
        self.assertIn('Improve the project documentation', row['request'])
        self.assertEqual(self.tools.inspect(row['id'])['status'], 'queued')
        self.assertEqual(self.tools.list()['objectives'][0]['id'], row['id'])
        self.tools.manage(row['id'], 'cancel', '', 'cancellation')
        self.assertEqual(self.store.get(row['id'])['status'], 'cancelled')

    def test_missing_or_wrong_owner_event_cannot_start_work(self):
        invalid = dict(self.request, request_thread='another-thread')
        with self.assertRaises(PermissionError):
            WorkflowTools(self.home, self.config, invalid).start('project', 'Edit', False, 'bad')
        self.body['event']['user'] = 'UOTHER'
        self.store.enqueue_slack('other-event', self.body)
        invalid = dict(self.request, request_event='other-event', owner_request={'event': 'other-event'})
        with self.assertRaises(PermissionError):
            WorkflowTools(self.home, self.config, invalid).list()
        self.assertEqual(self.store.list(), [])

    def test_no_publication_approval_tool_and_disabled_browser_not_exposed(self):
        tools = shared_tools(self.home, self.config, Documents(self.home, owner_key(self.config)), self.request)
        self.assertIn('development.start', tools.tools)
        self.assertNotIn('browser.start', tools.tools)
        self.assertFalse(any('approve' in name or 'merge' in name for name in tools.tools))
        with self.assertRaises(ValueError):
            self.tools.manage('a'*16, 'approve', '', 'bad')
        with self.assertRaises(PermissionError):
            self.tools.browse('Open a website', 'bad')

    def test_shared_tool_can_handoff_from_a_background_thread(self):
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(self.tools.start, 'project', 'Clarify README', False, 'worker').result()
        self.assertEqual(self.store.get(result['objective_id'])['status'], 'queued')

    def test_handoff_keeps_prepared_image_evidence_without_restarting_extraction(self):
        self.request['message'] = 'Owner request. Image evidence: setup button is mislabeled.'
        with patch('capo.slack_images.context', side_effect=AssertionError('No second extraction')):
            result = self.tools.start('project', 'Fix the label', False, 'image-work')
        self.assertIn('setup button is mislabeled', self.store.get(result['objective_id'])['request'])

    def test_prepare_binds_approval_only_after_host_preview_delivery(self):
        from capo.slack import SlackService
        result=self.tools.start('project','Clarify README',False,'start')
        id=result['objective_id'];row=self.store.get(id)
        self.config['repositories']['project']['allow_publication']=True
        git(Path(row['repo']),'remote','add','origin','https://github.com/example/project.git')
        publication={'digest':'a'*64,'payload':{'title':'Clarify setup','repository':'example/project',
            'base_branch':'main','commit':'b'*40,'body':'Tests passed.'}}
        row['publication']=publication;self.store.save(row,'fixture')
        directory=self.home/'artifacts'/id;directory.mkdir(parents=True)
        (directory/'changes.patch').write_text('-Old\n+New\n')
        with patch('capo.github.prepare',return_value=publication) as prepare,patch('capo.github.publish') as publish:
            prepared=self.tools.prepare(id,'review')
            self.assertTrue(prepared['prepared'])
            self.assertEqual(self.tools.prepare(id,'review'),prepared)
            prepare.assert_called_once()
            publish.assert_not_called()
        self.assertNotIn('slack_review_digest',self.store.get(id))
        client=Mock();service=SlackService(self.store,self.config,client)
        service.capability_conversation=Mock()
        service.capability_conversation.poll.return_value={'reply':'The other requested result is ready.'}
        with patch.object(service,'send_chunk',return_value=False):service.process_messages()
        self.assertNotIn('slack_review_digest',self.store.get(id))
        stored=json.loads(self.store.db.execute('SELECT data FROM slack_deliveries WHERE event_id=?',('owner-event',)).fetchone()[0])
        self.assertIn('other requested result',stored['text'])
        self.assertIn('Ready for review: Clarify setup',stored['text'])
        self.assertEqual(stored['review'],[id,'a'*64])
        # A restart can finish delivering the frozen host preview, then bind it.
        resumed=SlackService(self.store,self.config,client)
        with patch.object(resumed,'send_chunk',return_value=True):resumed.process_messages()
        self.assertEqual(self.store.get(id)['slack_review_digest'],'a'*64)

    def test_prepare_rejects_other_thread_and_disabled_publication(self):
        result=self.tools.start('project','Clarify README',False,'start');id=result['objective_id']
        with self.assertRaisesRegex(ValueError,'publication is not enabled'):self.tools.prepare(id,'disabled')
        row=self.store.get(id);row['slack']['thread_ts']='999.999';self.store.save(row,'fixture')
        with self.assertRaisesRegex(ValueError,'objective thread'):self.tools.prepare(id,'wrong-thread')


if __name__ == '__main__':
    unittest.main()
