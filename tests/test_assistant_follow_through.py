import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from capo.attention import personal_tasks
from capo.capabilities import owner_key, shared_tools, Documents
from capo.digest import DigestStore, scope
from capo.health import Health
from capo.personal_memory import PersonalMemory
from capo.personalization import snapshot
from capo.request_memory import RequestMemory
from capo.schedules import Schedules
from capo.tasks import Tasks
from test_tasks import fields


class AssistantFollowThroughTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.config = {'team_id':'T123','channel_id':'C123','owner_user_id':'U123'}
        self.owner = owner_key(self.config)
        self.now = datetime.now(timezone.utc)

    def test_tasks_inventory_and_morning_loose_ends(self):
        tasks = Tasks(self.home, self.owner)
        one = tasks.save('', '', fields(title='Read the proposal'), 'one')['task']
        tasks.save('', '', fields(title='Prepare report', timezone='UTC', due_at=(self.now-timedelta(hours=1)).isoformat()), 'two')
        tasks.save('', '', fields(title='Paused work', status='paused'), 'three')
        overview = tasks.overview(self.now)
        self.assertEqual(overview['counts']['unscheduled'], 1)
        self.assertEqual(overview['counts']['overdue'], 1)
        morning = personal_tasks(self.home, self.config, self.now)
        self.assertIn(one['id'], [t['task_id'] for t in morning])
        hourly = personal_tasks(self.home, self.config, self.now, heartbeat=True)
        self.assertNotIn(one['id'], [t['task_id'] for t in hourly])
        tasks.save(one['id'], '1', fields(title='Read the proposal',status='completed'), 'done')
        self.assertEqual(tasks.overview(self.now)['counts']['unscheduled'], 0)
        self.assertEqual(Tasks(self.home,'other').overview(self.now)['inspected'],0)

    def test_review_time_is_actionable_without_deadline(self):
        tasks = Tasks(self.home, self.owner)
        task = tasks.save('', '', fields(timezone='UTC'), 'one')['task']
        details = dict(task['follow_through'], next_review_at=(self.now-timedelta(days=1)).isoformat(),next_action='Check response')
        tasks.follow_through(task['id'],'1',details,'review')
        self.assertEqual(tasks.overview(self.now)['counts']['review_due'],1)
        self.assertEqual(personal_tasks(self.home,self.config,self.now,heartbeat=True)[0]['next_action'],'Check response')

    def test_contextual_feedback_replay_correction_and_forget(self):
        origin={'text':'Less of this, please. Actually, more like this.', 'event':'feedback'}
        memory=PersonalMemory(self.home,self.owner,origin,{'reply':'Example Trio concert'})
        result=memory.feedback('music.example','','Less of this, please.','Example Trio concert','less','one')
        self.assertEqual(result,memory.feedback('music.example','','Less of this, please.','Example Trio concert','less','one'))
        ctx=snapshot(self.home,self.config,self.home/'first')
        self.assertEqual(ctx['interests'][0]['feedback']['direction'],'less')
        memory.feedback('music.example','1','Actually, more like this.','Example Trio concert','more','two')
        self.assertEqual(snapshot(self.home,self.config,self.home/'second')['interests'][0]['feedback']['direction'],'more')
        # A retry retains its original context, while subsequent runs use the correction.
        self.assertEqual(snapshot(self.home,self.config,self.home/'first'),ctx)
        memory.forget('music.example','2','forget')
        self.assertEqual(snapshot(self.home,self.config,self.home/'third')['interests'],[])
        self.assertEqual(PersonalMemory(self.home,'other').search('')['memories'],[])

    def test_feedback_rejects_invented_subject_and_quote(self):
        memory=PersonalMemory(self.home,self.owner,{'text':'Less of this.'},{'reply':'Example article'})
        with self.assertRaises(ValueError):memory.feedback('news.test','','Less of this.','Invented subject','less','bad')
        with self.assertRaises(ValueError):memory.feedback('news.test','','I hate all news.','Example article','avoid','bad')
        with self.assertRaises(ValueError):memory.feedback('news.test','','Less of this.','Example article','invented','bad')

    def test_shared_feedback_uses_saved_thread_and_is_absent_from_automations(self):
        RequestMemory(self.home,self.owner,'thread').record('prior','outcome',{'reply':'Example concert'})
        request={'request_thread':'thread','owner_request':{'text':'More like this.', 'event':'next'}}
        registry=shared_tools(self.home,self.config,Documents(self.home,self.owner),request)
        registry.call('memory.feedback', {'key':'music.example','expected_revision':'','statement':'More like this.','subject':'Example concert','direction':'more'}, operation_id='feedback')
        self.assertEqual(PersonalMemory(self.home,self.owner).search('')['memories'][0]['feedback']['subject'],'Example concert')
        self.assertIn('tasks.overview',registry.tools)
        self.assertIn('health.status',registry.tools)
        self.assertNotIn('memory.feedback',shared_tools(self.home,self.config,Documents(self.home,self.owner)).tools)

    def test_connection_probe_refresh_failure_recovery_and_staleness(self):
        config=dict(self.config,gmail={'enabled':True,'drafts':True})
        health=Health(self.home,config)
        self.assertEqual(health.status()['connections'][0]['status'],'not_checked')
        from capo.providers import ServiceAuthenticationError
        with patch('capo.gmail.Gmail',side_effect=ServiceAuthenticationError('Gmail')):
            failed=health.check('gmail')
        self.assertEqual(failed['status'],'authentication')
        self.assertEqual(failed['consecutive_failures'],1)
        with patch('capo.gmail.Gmail') as provider:
            provider.return_value.drafts_enabled=True
            self.assertEqual(health.check('gmail')['status'],'healthy')
            self.assertEqual(provider.return_value.get.call_count,2)
            provider.return_value.draft_write.assert_not_called()
        self.assertTrue(health.status(self.now+timedelta(hours=3))['connections'][0]['stale'])
        self.assertEqual(Health(self.home,self.config).check('calendar')['status'],'disabled')

    def test_failed_connection_never_exposes_raw_error(self):
        h=Health(self.home,self.config)
        result=h.record('gmail',RuntimeError('private credential value'))
        self.assertNotIn('private credential',json.dumps(result))
        self.assertEqual(result['status'],'unavailable')
        self.assertEqual(h.path.stat().st_mode & 0o777,0o600)

    def test_health_alert_does_not_require_a_working_model(self):
        from unittest.mock import Mock
        from capo.heartbeat import select
        provider=Mock();provider.call.side_effect=RuntimeError('Provider down')
        result=select([{'id':'health','kind':'connection','title':'Gmail check unavailable',
                       'next_action':'Reconnect Gmail.'}],{},self.now,self.home/'run',provider)
        self.assertIn('Reconnect Gmail.',result['text'])
        provider.call.assert_not_called()
        self.assertEqual(select([{'id':'health','kind':'connection','title':'Gmail check unavailable'}],
            {'health':{'day':self.now.date().isoformat()}},self.now,self.home/'retry',provider)['text'],'')

    def test_weekend_health_uses_last_weekday_and_ignores_previews(self):
        config=dict(self.config,heartbeat={'enabled':True,'weekdays':list('01234'),'start_hour':10,'end_hour':16,'timezone':'UTC'})
        db=DigestStore(self.home);self.addCleanup(db.close)
        db.configure(scope(config),{'enabled':True,'weekdays':list('01234'),'timezone':'UTC','time':'07:00'})
        sunday=datetime(2030,1,6,17,tzinfo=timezone.utc)
        db.save({'key':'preview','scope':scope(config),'day':'2030-01-04','status':'sent','preview':True},sunday)
        checks=Health(self.home,config).automations(sunday)
        self.assertEqual(len(checks),2)
        self.assertTrue(all(c['due_at'].startswith('2030-01-04') for c in checks))
        self.assertTrue(all(c['status']=='missed' for c in checks))

    def test_calendar_probe_is_read_only(self):
        config=dict(self.config,calendar={'enabled':True})
        with patch('capo.calendar.GoogleCalendar') as provider:
            self.assertEqual(Health(self.home,config).check('calendar')['status'],'healthy')
            provider.return_value.events.assert_called_once()
            self.assertEqual(len(provider.return_value.method_calls),1)

    def test_missing_schedule_respects_window_then_success_and_partial(self):
        store=Schedules(self.home,self.owner)
        schedule=store.save('','',dict(title='Weekly review',request='Review saved tasks',weekdays=list('0123456'),time='10:00',timezone='UTC',enabled=True,catch_up_hours='6'),'schedule')['schedule']
        tomorrow=(self.now+timedelta(days=1)).replace(hour=10,minute=0,second=0,microsecond=0)
        h=Health(self.home,self.config)
        self.assertEqual(h.automations(tomorrow+timedelta(hours=2))[0]['status'],'not_started')
        self.assertEqual(h.automations(tomorrow+timedelta(hours=7))[0]['status'],'missed')
        db=DigestStore(self.home/'scheduled');self.addCleanup(db.close)
        run={'key':'run','scope':scope(self.config),'day':tomorrow.date().isoformat(),'status':'sent', 'schedule_id':schedule['id'],'revision':1}
        db.save(run,tomorrow)
        self.assertEqual(h.automations(tomorrow+timedelta(hours=7))[0]['status'],'sent')
        run['outcome_status']='partial';db.save(run,tomorrow)
        self.assertEqual(h.automations(tomorrow+timedelta(hours=7))[0]['status'],'needs_attention')
        store.save(schedule['id'],'1',dict((k,v) for k,v in schedule.items() if k not in ('id','revision','created_at','updated_at'))|{'enabled':False},'off')
        self.assertEqual(h.automations(tomorrow+timedelta(hours=7)),[])
