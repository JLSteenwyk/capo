import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from capo.tasks import Tasks, instant, next_occurrence
from capo.capabilities import shared_tools, Documents


def fields(**changes):
    value=dict(title='Prepare meeting', notes='', project='Example', status='open', waiting_on='',
               priority='normal', due_at='', remind_at='', timezone='America/Los_Angeles',
               recurrence='', dependencies=[], sources=[])
    value.update(changes)
    return value


class TaskTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.home=Path(self.temp.name); self.tasks=Tasks(self.home,'owner')

    def test_create_reschedule_restart_replay_and_stale_revision(self):
        first=self.tasks.save('', '', fields(remind_at='2026-09-18T09:00:00-07:00'), 'event:0')
        other=Tasks(self.home,'owner')
        self.assertEqual(first,other.save('', '', fields(remind_at='2026-09-18T09:00:00-07:00'),'event:0'))
        id=first['task']['id']
        changed=other.save(id,'1',fields(remind_at='2026-09-19T10:00:00-07:00'),'event2:0')
        self.assertEqual(changed['task']['revision'],2)
        with self.assertRaises(ValueError):other.save(id,'1',fields(),'stale')
        self.assertEqual(len(other.search()['tasks']),1)
        self.assertEqual(Tasks(self.home,'different-owner').search()['tasks'],[])
        with self.assertRaises(ValueError):other.save('', '', fields(title='different'),'event:0')

    def test_dependencies_completion_cancel_and_cycles(self):
        a=self.tasks.save('','',fields(),'a')['task']
        b=self.tasks.save('','',fields(dependencies=[a['id']]),'b')['task']
        with self.assertRaises(ValueError):self.tasks.save(a['id'],'1',fields(dependencies=[b['id']]),'cycle')
        with self.assertRaises(ValueError):self.tasks.save(b['id'],'1',fields(status='completed',dependencies=[a['id']]),'early')
        self.tasks.save(a['id'],'1',fields(status='completed'),'done')
        self.tasks.save(b['id'],'1',fields(status='completed',dependencies=[a['id']]),'done2')
        self.assertEqual(self.tasks.search()['tasks'],[])
        self.assertEqual(len(self.tasks.search(status='completed')['tasks']),2)
        self.tasks.save(b['id'],'2',fields(status='cancelled'),'cancel')
        self.assertEqual(len(self.tasks.search(status='cancelled')['tasks']),1)

    def test_dates_dst_recurrence_and_invalid_times(self):
        for bad in ['2026-03-08T02:30:00-08:00','2026-09-14T09:00:00']:
            with self.assertRaises(ValueError):instant(bad,'America/Los_Angeles')
        self.assertEqual(next_occurrence('2026-03-07T09:00:00-08:00','America/Los_Angeles','daily'),
                         '2026-03-08T09:00:00-07:00')
        self.assertEqual(next_occurrence('2026-03-07T02:30:00-08:00','America/Los_Angeles','daily'),
                         '2026-03-08T03:30:00-07:00')
        a=self.tasks.save('','',fields(due_at='2026-09-18T09:00:00-07:00',recurrence='weekdays'),'a')['task']
        f=fields(due_at=a['due_at'],recurrence='weekdays',status='completed')
        next_task=self.tasks.save(a['id'],'1',f,'done')['task']
        self.assertEqual(next_task['id'],a['id'])
        self.assertEqual(next_task['due_at'],'2026-09-21T09:00:00-07:00')
        self.assertEqual(next_task['status'],'open')

    def test_composable_registry_and_receipt_required(self):
        registry=shared_tools(self.home,{},Documents(self.home,'local'))
        args={'id':'','expected_revision':'','fields':fields(sources=['gmail:synthetic-message'])}
        with self.assertRaises(ValueError):registry.call('tasks.save',args)
        result=registry.call('tasks.save',args,operation_id='owner-event:0')
        read=registry.call('tasks.get',{'id':result['task']['id']})
        self.assertEqual(read['sources'],['gmail:synthetic-message'])
        self.assertIn('now',registry.call('clock.now',{}))
