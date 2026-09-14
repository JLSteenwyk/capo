import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from capo.tasks import Tasks, FOLLOW_THROUGH
from capo.attention import personal_tasks
from capo.capabilities import owner_key
from test_tasks import fields


def details(**changes):
    result = {k: [] if schema['type'] == 'array' else ''
              for k, schema in FOLLOW_THROUGH['properties'].items()}
    result.update(changes)
    return result


class FollowThroughTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.config = {'calendar': {'timezone': 'America/Los_Angeles'}}
        self.tasks = Tasks(self.home, owner_key(self.config))

    def test_cross_source_update_retains_original_and_revision_history(self):
        task = self.tasks.save('', '', fields(sources=['mail:proposal']), 'create')['task']
        updated = self.tasks.follow_through(task['id'], '1', details(
            outcome='Decide on proposal', original_conversation='slack:original',
            next_action='Review the revised proposal', decisions=['Wait for the revision'],
            next_review_at='2030-02-05T10:00:00-08:00'), 'review')['task']
        updated = self.tasks.save(task['id'], '2', fields(sources=['mail:proposal', 'github:revision']), 'link')['task']
        self.assertEqual(updated['follow_through']['original_conversation'], 'slack:original')
        self.assertEqual(len(self.tasks.history(task['id'])['history']), 3)
        duplicate = self.tasks.save('', '', fields(sources=['github:revision']), 'another-check')
        self.assertEqual(duplicate['task']['id'], task['id'])
        self.assertFalse(duplicate['saved'])
        self.assertEqual(len(self.tasks.search()['tasks']), 1)

    def test_candidates_paused_dismissed_and_closed_items_do_not_alert(self):
        for status in ('candidate', 'paused', 'dismissed', 'completed', 'cancelled'):
            self.tasks.save('', '', fields(title=status, status=status, priority='high'), status)
        self.assertEqual(self.tasks.search()['tasks'], [])
        self.assertEqual(len(self.tasks.search(status='all')['tasks']), 5)
        self.assertEqual(personal_tasks(self.home, self.config, datetime.now(timezone.utc), heartbeat=True), [])

    def test_completed_dependency_makes_waiting_work_ready_without_model_edit(self):
        prerequisite = self.tasks.save('', '', fields(title='Receive proposal'), 'a')['task']
        task = self.tasks.save('', '', fields(status='waiting', dependencies=[prerequisite['id']]), 'b')['task']
        self.assertFalse(self.tasks.get(task['id'])['ready'])
        self.tasks.save(prerequisite['id'], '1', fields(title='Receive proposal', status='completed'), 'finish')
        self.assertTrue(self.tasks.get(task['id'])['ready'])
        self.assertEqual(self.tasks.get(task['id'])['blocked_by'], [])

    def test_replay_scope_and_stale_review(self):
        task = self.tasks.save('', '', fields(), 'task')['task']
        result = self.tasks.follow_through(task['id'], '1', details(next_action='Read evidence'), 'review')
        restarted = Tasks(self.home, owner_key(self.config))
        self.assertEqual(restarted.follow_through(task['id'], '1', details(next_action='Read evidence'), 'review'), result)
        with self.assertRaises(ValueError):
            restarted.follow_through(task['id'], '1', details(), 'stale')
        with self.assertRaises(ValueError):
            Tasks(self.home, 'another-owner').get(task['id'])
        with self.assertRaises(ValueError):
            restarted.follow_through(task['id'], '2', details(next_review_at='2030-01-01T10:00:00'), 'bad-date')

    def test_dismissed_source_cannot_be_recreated_by_repeated_discovery(self):
        task = self.tasks.save('', '', fields(status='candidate', sources=['mail:idea']), 'discover')['task']
        self.tasks.save(task['id'], '1', fields(status='dismissed', sources=['mail:idea']), 'dismiss')
        again = self.tasks.save('', '', fields(status='candidate', sources=['mail:idea']), 'rediscover')['task']
        self.assertEqual(again['status'], 'dismissed')
        self.assertEqual(again['id'], task['id'])
