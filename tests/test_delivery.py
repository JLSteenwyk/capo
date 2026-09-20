import tempfile
import unittest
from pathlib import Path

from capo.delivery import deliver_routine
from capo.communication import completed_message, plan_message
from capo.repository import git
from capo.store import Store
from test_github import FakeGitHub


class DeliveryCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.repo = root / 'repo'
        self.repo.mkdir()
        git(self.repo, 'init', '-b', 'main')
        git(self.repo, 'config', 'user.name', 'Test')
        git(self.repo, 'config', 'user.email', 'test@example.invalid')
        git(self.repo, 'remote', 'add', 'origin', 'https://github.com/owner/project.git')
        (self.repo / 'parser.py').write_text('def parse(value):\n    return value == "true"\n')
        git(self.repo, 'add', '.')
        git(self.repo, 'commit', '-m', 'fixture')
        base = git(self.repo, 'rev-parse', 'HEAD')
        (self.repo / 'parser.py').write_text('def parse(value):\n    return value in ("true", "yes")\n')
        git(self.repo, 'add', '.')
        self.store = Store(root / 'state')
        self.addCleanup(self.store.db.close)
        self.objective = self.store.create({'repo': str(self.repo), 'workspace': str(self.repo), 'base': base})
        self.objective.update(status='completed', accepted_tree=git(self.repo, 'write-tree'), request='PRIVATE REQUEST',
                              plan={'summary': 'PRIVATE SUMMARY'}, verification={
                                  'checks': [{'passed': True}], 'reviews': [{'approved': True, 'provider': 'grok'}],
                                  'decision': {'accepted': True}})
        self.store.save(self.objective, 'fixture')
        (self.store.home / 'artifacts' / self.objective['id']).mkdir(parents=True)
        self.settings = {'path': str(self.repo), 'allow_publication': True, 'auto_publish_routine': True}
        self.gateway = FakeGitHub(base)

    def deliver(self):
        deliver_routine(self.store, self.objective['id'], self.settings, self.gateway)
        return self.store.get(self.objective['id'])

    def test_accepted_small_change_publishes_once_without_preview(self):
        result = self.deliver()
        self.assertEqual(result['publication']['status'], 'published')
        self.assertTrue(result['publication']['pr']['isDraft'])
        self.assertNotIn('PRIVATE', result['publication']['payload']['body'])
        self.assertNotIn('slack_review_digest', result)
        self.deliver()
        self.assertEqual((self.gateway.pushes, self.gateway.creates), (1, 1))
        self.assertIn('/pull/1', completed_message(result))
        self.assertNotIn('prepare', completed_message(result))

    def test_default_policy_does_not_publish(self):
        del self.settings['auto_publish_routine']
        self.assertNotIn('routine_delivery', self.deliver())
        self.assertEqual(self.gateway.pushes, 0)

    def test_new_function_needs_review(self):
        with (self.repo / 'parser.py').open('a') as handle:
            handle.write('\ndef new_function():\n    return 1\n')
        git(self.repo, 'add', '.')
        self.objective['accepted_tree'] = git(self.repo, 'write-tree')
        self.store.save(self.objective, 'fixture')
        self.assertEqual(self.deliver()['routine_delivery']['status'], 'review')
        self.assertEqual(self.gateway.pushes, 0)

    def test_failed_evidence_never_publishes(self):
        self.objective['verification']['decision']['accepted'] = False
        self.store.save(self.objective, 'fixture')
        self.assertEqual(self.deliver()['routine_delivery']['status'], 'review')
        self.assertEqual(self.gateway.pushes, 0)

    def test_new_owner_input_prevents_publication(self):
        self.store.add_followup(self.objective['id'], 'EvNext', 'Change the scope')
        self.assertNotIn('publication', self.deliver())
        self.assertEqual(self.gateway.pushes, 0)

    def test_uncertain_network_result_does_not_repeat(self):
        self.gateway.timeout_after_create = True
        result = self.deliver()
        self.assertEqual(result['routine_delivery']['status'], 'failed')
        self.deliver()
        self.assertEqual(self.gateway.creates, 1)
        self.assertIn("couldn't confirm it reached GitHub", completed_message(result))

    def test_plan_does_not_dump_internal_workflow(self):
        text = plan_message({'plan': {'summary': 'The seeded candidate in src/helper.py ' * 50}})
        self.assertLess(len(text.split()), 40)
        self.assertNotIn('src/', text)
        self.assertNotIn('candidate', text)

    def test_feature_scope_publishes_new_functions_with_existing_checks(self):
        self.settings['automatic_change_scope']='features'
        with (self.repo/'parser.py').open('a') as f:f.write('\ndef feature():\n    return 1\n')
        git(self.repo,'add','.')
        self.objective['accepted_tree']=git(self.repo,'write-tree')
        self.store.save(self.objective,'feature')
        result=self.deliver()
        self.assertEqual(result['publication']['status'],'published')
        self.assertNotIn('PRIVATE',result['publication']['payload']['body'])

    def test_feature_scope_never_bypasses_failed_review(self):
        self.settings['automatic_change_scope']='features'
        self.objective['verification']['reviews'][0]['approved']=False
        self.store.save(self.objective,'failed_review')
        self.assertEqual(self.deliver()['routine_delivery']['status'],'review')
        self.assertEqual(self.gateway.pushes,0)

    def test_publication_rechecks_metadata_even_after_preparation(self):
        from capo.github import prepare,publish,payload_digest
        prepared=prepare(self.store,self.objective['id'],'owner/project','main',title='Safe title',body='Safe body')
        row=self.store.get(self.objective['id'])
        marker='xapp-'+'synthetic-not-real'
        row['publication']['payload']['body']=marker
        row['publication']['digest']=payload_digest(row['publication']['payload'])
        self.store.save(row,'unsafe_metadata_fixture')
        with self.assertRaisesRegex(ValueError,'private information'):
            publish(self.store,row['id'],row['publication']['digest'],self.gateway)
        self.assertEqual(self.gateway.pushes,0)

    def test_preparation_rejects_private_content_and_runtime_files(self):
        from capo.github import prepare
        marker='xapp-'+'synthetic-not-real'
        with self.assertRaisesRegex(ValueError,'private information'):
            prepare(self.store,self.objective['id'],'owner/project','main',title='Safe',body=marker)
        (self.repo/'transcripts').mkdir()
        (self.repo/'transcripts/private.md').write_text('Synthetic private record')
        git(self.repo,'add','.')
        self.objective['accepted_tree']=git(self.repo,'write-tree')
        self.store.save(self.objective,'private_file')
        with self.assertRaisesRegex(ValueError,'runtime artifacts'):
            prepare(self.store,self.objective['id'],'owner/project','main',title='Safe',body='Safe')
        self.assertEqual(self.gateway.pushes,0)
