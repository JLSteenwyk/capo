import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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


if __name__ == '__main__':
    unittest.main()
