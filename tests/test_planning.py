import tempfile
import time
import unittest
from datetime import datetime,timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from capo.availability import availability
from capo.schedules import Schedules,due_slot
from capo.scheduled_requests import ScheduledManager
from capo.capabilities import owner_key,Documents,shared_tools
from capo.tasks import Tasks
from test_tasks import fields


def settings(**changes):
    value=dict(title='Weekly plan',request='Plan my week from tasks, calendar and email.',weekdays=['6'],time='17:00',timezone='America/Los_Angeles',enabled=True,catch_up_hours='24')
    value.update(changes);return value


class PlanningTests(unittest.TestCase):
    def test_availability_conflicts_and_transparent_events(self):
        def event(id,a,b,**extra):return dict(id=id,start={'dateTime':'2026-09-14T'+a+':00-07:00'},end={'dateTime':'2026-09-14T'+b+':00-07:00'},**extra)
        events=[event('a','10:00','11:00'),event('b','10:30','12:00'),event('free','13:00','14:00',transparency='transparent')]
        result=availability(events,'2026-09-14T09:00:00-07:00','2026-09-14T17:00:00-07:00','America/Los_Angeles','09:00','17:00',['0'],'30')
        self.assertEqual([w['minutes'] for w in result['free_windows']],[60,300])
        self.assertEqual(result['conflicts'][0]['event_ids'],['a','b'])
        self.assertIn('No time was booked',result['coverage'])

    def test_all_day_and_dst_windows(self):
        result=availability([{'id':'away','start':{'date':'2026-03-09'},'end':{'date':'2026-03-10'}}],
            '2026-03-08T00:00:00-08:00','2026-03-10T00:00:00-07:00','America/Los_Angeles','09:00','17:00',['0','6'],'30')
        self.assertEqual(len(result['free_windows']),1)
        self.assertTrue(result['free_windows'][0]['start'].endswith('-07:00'))

    def test_sunday_slot_catchup_dst_and_creation_boundary(self):
        s=dict(settings(),created_at='2026-01-01T00:00:00+00:00')
        for value,offset in [('2026-03-01T17:00:00-08:00','-08:00'),('2026-03-08T17:00:00-07:00','-07:00')]:
            now=datetime.fromisoformat(value);self.assertEqual(due_slot(s,now)[0],now)
            self.assertIsNotNone(due_slot(s,now+timedelta(hours=12)))
            self.assertIsNone(due_slot(s,now+timedelta(hours=25)))
        s['created_at']='2026-03-09T00:30:00+00:00'
        self.assertIsNone(due_slot(s,datetime.fromisoformat('2026-03-09T09:00:00-07:00')))

    def test_shared_task_deadline_change_updates_planning_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp);tasks=Tasks(home,owner_key({}))
            task=tasks.save('','',fields(due_at='2026-09-18T17:00:00-07:00'),'task')['task']
            tools=shared_tools(home,{},Documents(home,owner_key({})))
            args={'query':'','status':'active','cursor':''}
            self.assertEqual(tools.call('tasks.search',args)['tasks'][0]['due_at'],task['due_at'])
            tasks.save(task['id'],'1',fields(due_at='2026-09-15T17:00:00-07:00'),'change')
            current=tools.call('tasks.search',args)['tasks'][0]
            self.assertEqual(current['id'],task['id']);self.assertEqual(current['due_at'],'2026-09-15T17:00:00-07:00')

    def test_scheduled_request_readonly_and_one_delivery_after_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp);config={'team_id':'T123','channel_id':'C123','owner_user_id':'U123'}
            schedules=Schedules(home,owner_key(config))
            value=schedules.save('','',settings(),'schedule')['schedule']
            now=datetime.fromisoformat('2030-01-06T17:00:00-08:00')
            client=Mock();client.chat_postMessage.return_value={'ts':'synthetic'}
            service=SimpleNamespace(store=SimpleNamespace(home=home),config=config,client=client,bot_user_id='BOT')
            result={'reply':'Your week has room for the outline on Monday.','document_title':'','document':'','receipts':[]}
            observed=[]
            def generate(provider,tools,request,*args,**kwargs):
                observed.append(tools)
                self.assertTrue(all(not t.mutates for t in tools.tools.values()))
                self.assertEqual(request['message'],value['request']);return result
            manager=ScheduledManager(service)
            try:
                with patch('capo.scheduled_requests.research',side_effect=generate):
                    manager.tick(now)
                    for worker in manager.workers.values():worker.join(3);self.assertFalse(worker.is_alive())
                    manager.tick(now+timedelta(seconds=30))
                self.assertEqual(client.chat_postMessage.call_count,1)
                restarted=ScheduledManager(service)
                try:restarted.tick(now+timedelta(minutes=2))
                finally:restarted.db.close()
                self.assertEqual(client.chat_postMessage.call_count,1)
                self.assertIn('tasks.search',observed[0].tools)
                self.assertNotIn('tasks.save',observed[0].tools)
            finally:manager.db.close()
