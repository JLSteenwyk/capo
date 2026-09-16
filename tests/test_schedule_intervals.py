import tempfile
import unittest
from datetime import datetime,timedelta,date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch

from capo.capabilities import owner_key
from capo.schedules import Schedules,due_slot,matches_day
from capo.scheduled_requests import ScheduledManager
from capo.team_status import next_check
from test_team_assignments import fields


class IntervalTests(unittest.TestCase):
    def test_adjacent_anchors_alternate_across_weeks_months_and_dst(self):
        shopping=dict(fields(interval_days='2',anchor_date='2030-03-01',time='11:00'),created_at='2029-01-01T00:00:00+00:00')
        style=dict(shopping,anchor_date='2030-03-02')
        from zoneinfo import ZoneInfo
        zone=ZoneInfo('America/Los_Angeles')
        for i in range(70):
            day=date(2030,3,1)+timedelta(days=i)
            now=datetime.combine(day,datetime.min.time(),zone).replace(hour=11)
            slots=[due_slot(s,now) for s in (shopping,style)]
            self.assertEqual(sum(x is not None for x in slots),1)
            self.assertIsNotNone(slots[i%2])
        self.assertFalse(matches_day(shopping,date(2030,2,28)))

    def test_next_check_and_alternating_weeks(self):
        s=fields(interval_days='14',anchor_date='2030-01-04',weekdays=['4'],time='11:00')
        self.assertEqual(next_check(s,datetime.fromisoformat('2030-01-04T11:01:00-08:00')),
                         '2030-01-18T11:00:00-08:00')
        self.assertIsNone(next_check(dict(s,enabled=False),datetime.now().astimezone()))
        self.assertEqual(next_check(s,datetime.fromisoformat('2028-01-01T00:00:00-08:00')),
                         '2030-01-04T11:00:00-08:00')
        other=dict(s,anchor_date='2030-01-11')
        for week in range(8):
            now=datetime.fromisoformat('2030-01-04T11:00:00-08:00')+timedelta(days=7*week)
            pair=[dict(x,created_at='2029-01-01T00:00:00+00:00') for x in (s,other)]
            self.assertEqual([due_slot(x,now) is not None for x in pair],
                             [week%2==0,week%2==1])

    def test_interval_validation_and_edits_preserve_cadence(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Schedules(Path(tmp),'owner')
            for changes in ({'interval_days':'0'}, {'interval_days':'29'}, {'interval_days':'2'},
                            {'interval_days':'2','anchor_date':'2030-02-30'},
                            {'interval_days':'7','anchor_date':'2030-01-04','weekdays':['0']}):
                with self.assertRaises(ValueError):store.save('','',fields(**changes),'bad')
            s=store.save('','',fields(interval_days='2',anchor_date='2030-01-04'),'create')['schedule']
            s=store.save(s['id'],'1',fields(time='12:00'),'edit')['schedule']
            self.assertEqual((s['interval_days'],s['anchor_date']),('2','2030-01-04'))
            self.assertEqual(s['time'],'12:00')
            s=store.save(s['id'],'2',fields(interval_days='1',anchor_date=''),'daily')['schedule']
            self.assertTrue(matches_day(s,date(2030,1,5)))

    def test_catchup_creation_boundary_and_no_old_replay(self):
        s=dict(fields(interval_days='2',anchor_date='2030-01-01',time='23:30'),created_at='2029-01-01T00:00:00+00:00')
        self.assertIsNotNone(due_slot(s,datetime.fromisoformat('2030-01-02T00:15:00-08:00')))
        self.assertIsNone(due_slot(s,datetime.fromisoformat('2030-01-02T00:31:00-08:00')))
        s['created_at']='2030-01-02T08:00:00+00:00'
        self.assertIsNone(due_slot(s,datetime.fromisoformat('2030-01-02T00:15:00-08:00')))

    def test_scheduler_starts_only_assigned_agent_each_day_and_restart_does_not_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp);config=dict(team_id='T',channel_id='C',owner_user_id='U')
            store=Schedules(home,owner_key(config))
            for role,anchor in (('shopping_assistant','2030-01-01'),('style_assistant','2030-01-02')):
                store.save('','',fields(agent=role,interval_days='2',anchor_date=anchor,time='11:00'),role)
            service=SimpleNamespace(store=SimpleNamespace(home=home),config=config,client=Mock(),bot_user_id='BOT')
            observed=[]
            def generate(provider,tools,request,*args,**kwargs):
                observed.append(request['agent'])
                tools.call('monitor.report',{'findings':[],'blockers':[],'coverage':'Synthetic saved preferences inspected.'},operation_id='report')
                return dict(reply='Nothing new.',document='',document_title='',receipts=[{'tool':'documents.list','result':{}}])
            with patch('capo.scheduled_requests.research',side_effect=generate):
                for day in range(4):
                    now=datetime.fromisoformat('2030-01-01T11:00:00-08:00')+timedelta(days=day)
                    manager=ScheduledManager(service)
                    try:
                        manager.tick(now)
                        for worker in manager.workers.values():worker.join(5);self.assertFalse(worker.is_alive())
                    finally:manager.db.close()
                    restarted=ScheduledManager(service)
                    try:restarted.tick(now+timedelta(minutes=1));self.assertFalse(restarted.workers)
                    finally:restarted.db.close()
            self.assertEqual(observed,['shopping_assistant','style_assistant']*2)
