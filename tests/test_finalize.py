import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from capo.finalize import request_merge, finish_delivery
from capo.store import Store


class FinalizeCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name))
        self.settings = {'allow_publication': True, 'merge_after_approval': True}
        self.o = self.store.create({'workspace': self.temp.name})
        self.o['publication'] = {'status': 'published', 'payload': {
            'repository': 'example/project', 'branch': f"capo/{self.o['id']}-{'b'*12}",
            'base_branch': 'main', 'commit': 'a'*40, 'tree': 'b'*40},
            'pr': {'number': 1, 'url': 'https://github.com/example/project/pull/1'}}
        self.store.save(self.o, 'fixture')
        self.remote = {'state': 'OPEN', 'headRefOid': 'a'*40, 'isDraft': False,
                       'mergeStateStatus': 'CLEAN', 'reviewDecision': '',
                       'statusCheckRollup': [{'conclusion': 'SUCCESS'}]}
        self.gateway = Mock(env={})
        self.gateway.status.return_value = self.remote
        self.gateway.remote_ref.return_value = 'a'*40

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def run_delivery(self):
        finish_delivery(self.store, self.o['id'], self.settings, self.gateway)

    def test_no_merge_without_authorization(self):
        self.run_delivery()
        self.gateway.status.assert_not_called()
        self.assertFalse(request_merge(self.store, self.o['id'], {}))

    def test_exact_head_merge_and_guarded_branch_deletion(self):
        request_merge(self.store, self.o['id'], self.settings)
        self.gateway.status.side_effect = [self.remote, dict(self.remote, state='MERGED')]
        with patch('capo.finalize.git') as git:
            self.run_delivery()
        self.gateway.gh.assert_called_once_with('pr', 'merge', '1', '--repo', 'example/project',
                                               '--squash', '--match-head-commit', 'a'*40)
        args = git.call_args.args
        self.assertIn('--force-with-lease=refs/heads/'+self.o['publication']['payload']['branch']+':'+'a'*40, args)
        self.assertEqual(self.store.get(self.o['id'])['merge_delivery']['status'], 'completed')
        self.run_delivery()
        self.assertEqual(self.gateway.gh.call_count, 1)

    def test_missing_or_pending_checks_wait(self):
        for checks in ([], [{'status': 'IN_PROGRESS'}]):
            request_merge(self.store, self.o['id'], self.settings)
            self.remote['statusCheckRollup'] = checks
            self.run_delivery()
            self.gateway.gh.assert_not_called()
        self.assertEqual(self.store.get(self.o['id'])['merge_delivery']['status'], 'waiting')

    def test_failed_checks_changed_head_and_review_block(self):
        for update in ({'statusCheckRollup': [{'conclusion': 'FAILURE'}]},
                       {'headRefOid': 'c'*40}, {'reviewDecision': 'CHANGES_REQUESTED'},
                       {'mergeStateStatus': 'DIRTY'}):
            with self.subTest(update=update):
                o = self.store.get(self.o['id']); o.pop('merge_delivery', None); self.store.save(o, 'reset')
                request_merge(self.store, self.o['id'], self.settings)
                self.gateway.status.return_value = dict(self.remote, **update)
                self.run_delivery()
                self.gateway.gh.assert_not_called()
                self.assertEqual(self.store.get(o['id'])['merge_delivery']['status'], 'blocked')

    def test_merged_pr_only_cleans_unchanged_branch(self):
        request_merge(self.store, self.o['id'], self.settings)
        self.gateway.status.return_value = dict(self.remote, state='MERGED')
        self.gateway.remote_ref.return_value = 'c'*40
        with patch('capo.finalize.git') as git:
            self.run_delivery()
        git.assert_not_called()
        self.gateway.gh.assert_not_called()
        self.assertEqual(self.store.get(self.o['id'])['merge_delivery']['status'], 'blocked')

    def test_draft_is_made_ready_before_merge(self):
        request_merge(self.store, self.o['id'], self.settings)
        self.remote['isDraft'] = True
        self.run_delivery()
        self.gateway.gh.assert_called_once_with('pr', 'ready', '1', '--repo', 'example/project')
