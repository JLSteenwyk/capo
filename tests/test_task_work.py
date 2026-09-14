import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from capo.capabilities import owner_key
from capo.research_tools import ReadTool, ReadTools
from capo.contracts import object_schema
from capo.tasks import Tasks
from capo.task_work import TaskWork
from capo.recovery import RetryLater
from test_tasks import fields
from test_follow_through import details


class TaskWorkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.config = {}
        self.now = datetime(2030, 1, 3, 18, tzinfo=timezone.utc)
        self.origin = {'event':'owner-request', 'thread':'conversation', 'text':'Create the requested calendar entry.'}
        self.tasks = Tasks(self.home, owner_key(self.config), self.origin, ['calendar.change','tasks.save','tasks.follow_through'])
        self.worker = TaskWork(self.home, self.config)
        self.write = Mock(return_value={'changed':True})
        self.registry = ReadTools([ReadTool('calendar.change', 'Personal event', object_schema({}), self.write, True)]+self.tasks.tools())

    def test_authorized_work_runs_once_and_verifies_before_completion(self):
        task = self.tasks.save('', '', fields(), 'task')['task']
        def perform(provider, tools, request, *args, **kwargs):
            self.assertEqual(request['message'], self.origin['text'])
            result = tools.call('calendar.change', {}, operation_id='change')
            tools.call('tasks.follow_through', {'id':task['id'], 'expected_revision':'1', 'details':details(outcome='Create calendar entry', completion_evidence=['calendar.change returned changed=true'])}, operation_id='evidence')
            tools.call('tasks.save', {'id':task['id'], 'expected_revision':'2', 'fields':fields(status='completed')}, operation_id='done')
            return {'reply':'Created the event.', 'document':'', 'document_title':'',
                    'receipts':[{'tool':'calendar.change','result':result}]}
        with patch('capo.task_work.shared_tools', return_value=self.registry), patch('capo.task_work.research', side_effect=perform) as run:
            items = self.worker.tick(self.now, [])
            self.assertEqual(items[0]['status'], 'completed')
            self.assertEqual(items[0]['verified_actions'], ['calendar.change'])
            pending = TaskWork(self.home, self.config).tick(self.now+timedelta(hours=1), [])
            self.assertEqual(pending, items)
            self.assertEqual(TaskWork(self.home, self.config).tick(self.now+timedelta(hours=1), [], {items[0]['id']: {}}), [])
            self.assertEqual(run.call_count, 1)
        self.write.assert_called_once()

    def test_observed_or_paused_work_stays_quiet(self):
        Tasks(self.home, owner_key(self.config)).save('', '', fields(), 'observed')
        self.tasks.save('', '', fields(title='Paused task', status='paused'), 'paused')
        with patch('capo.task_work.research') as run:
            self.assertEqual(self.worker.tick(self.now, []), [])
            run.assert_not_called()

    def test_retry_reuses_saved_request_and_operation_directory(self):
        self.tasks.save('', '', fields(), 'task')
        result = {'reply':'No changes needed.', 'document':'', 'document_title':'', 'receipts':[]}
        with patch('capo.task_work.shared_tools', return_value=self.registry), patch('capo.task_work.research', side_effect=[RetryLater(self.now.timestamp()+60), result]) as run:
            self.assertEqual(self.worker.tick(self.now, []), [])
            self.assertEqual(self.worker.tick(self.now+timedelta(seconds=30), []), [])
            self.worker.tick(self.now+timedelta(hours=1), [{'id':'new-evidence'}])
            first, second = run.call_args_list
            self.assertEqual(first.args[2:4], second.args[2:4])
            self.assertEqual(self.worker.tick(self.now+timedelta(hours=2), []), [])
            self.assertEqual(run.call_count, 2)

    def test_repeated_failure_stops_and_owner_followup_allows_recovery(self):
        task = self.tasks.save('', '', fields(), 'task')['task']
        with patch('capo.task_work.shared_tools', return_value=self.registry), patch('capo.task_work.research', side_effect=ValueError('private details')) as run:
            item = self.worker.tick(self.now, [])[0]
            self.assertNotIn('private', item['summary'])
            self.assertEqual(self.worker.tick(self.now+timedelta(hours=1), [], {item['id']: {}}), [])
            followup = dict(self.origin, event='updated', text='The connection is fixed; resume.')
            Tasks(self.home, owner_key(self.config), followup, ['calendar.change']).save(task['id'], '1', fields(), 'resume')
            self.worker.tick(self.now+timedelta(hours=2), [])
            self.assertEqual(run.call_count, 2)

    def test_budget_limits_tasks_per_check(self):
        for number in range(4):
            self.tasks.save('', '', fields(title='Task '+str(number)), str(number))
        result = {'reply':'No change.', 'document':'', 'document_title':'', 'receipts':[]}
        with patch('capo.task_work.shared_tools', return_value=self.registry), patch('capo.task_work.research', return_value=result) as run:
            self.worker.tick(self.now, [])
            self.assertEqual(run.call_count, 2)

    def test_relevant_new_evidence_wakes_work_once_before_its_next_review(self):
        self.tasks.save('', '', fields(sources=['email:message-a']), 'task')
        result = {'reply':'No further change.', 'document':'', 'document_title':'', 'receipts':[]}
        change = {'id':'updated', 'kind':'email', 'source_reference':'email:message-a', 'title':'Updated proposal'}
        with patch('capo.task_work.shared_tools', return_value=self.registry), patch('capo.task_work.research', return_value=result) as run:
            self.worker.tick(self.now, [])
            self.worker.tick(self.now+timedelta(hours=1), [change], changes=[change])
            self.worker.tick(self.now+timedelta(hours=2), [change], changes=[change])
            self.assertEqual(run.call_count, 2)
