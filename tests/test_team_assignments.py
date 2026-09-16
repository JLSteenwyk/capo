import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from capo.assignment_reports import AssignmentReport, finish, previous_report
from capo.capabilities import Documents, owner_key, shared_tools
from capo.digest import DigestStore, scope
from capo.schedules import Schedules
from capo.scheduled_requests import ScheduledManager
from capo.team_status import TeamStatus, next_check


def fields(**changes):
    value = dict(title='Renewal watch', request='Inspect renewal evidence.', weekdays=list('0123456'),
                 time='10:00', timezone='America/Los_Angeles', enabled=True, catch_up_hours='1',
                 agent='money_saver', delivery='changes')
    return dict(value, **changes)


def report(version='2029-12-31:20USD', **changes):
    return dict({'findings': [dict(key='renewal:sample', version=version, summary='Example renews for $20.',
                                  source='mail:synthetic')], 'blockers': [], 'coverage': 'Two messages inspected.'}, **changes)


class AssignmentTests(unittest.TestCase):
    def test_schedule_edits_keep_agent_and_delivery_legacy_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Schedules(Path(tmp),'owner')
            value=store.save('','',fields(),'create')['schedule']
            legacy=fields(enabled=False);del legacy['agent'];del legacy['delivery']
            edited=store.save(value['id'],'1',legacy,'pause')['schedule']
            self.assertEqual((edited['agent'],edited['delivery']),('money_saver','changes'))
            new=store.save('','',legacy,'legacy')['schedule']
            self.assertEqual((new['agent'],new['delivery']),('capo','always'))
            self.assertIsNone(next_check(edited,datetime.now(timezone.utc)))
            with self.assertRaises(ValueError):store.save(value['id'],'1',legacy,'stale')
            with self.assertRaises(ValueError):store.save('','',fields(agent='invented'),'bad')

    def test_next_check_tracks_local_dst(self):
        s=fields(weekdays=['6'])
        self.assertEqual(next_check(s,datetime.fromisoformat('2026-03-07T12:00:00-08:00')),
                         '2026-03-08T10:00:00-07:00')

    def test_stable_changes_quiet_reappearance_and_blockers(self):
        run={'title':'Watch'};finish(run,report(),{})
        self.assertEqual(run['status'],'ready');self.assertIn('$20',run['payload']['text'])
        self.assertNotIn('mail:synthetic',run['payload']['text'])
        same=report();same['findings'][0]['summary']='Different wording for same fact.'
        finish(run,same,report());self.assertEqual(run['status'],'quiet')
        finish(run,report('new-price'),report());self.assertEqual(run['status'],'ready')
        empty=report(findings=[]);finish(run,empty,report());self.assertEqual(run['status'],'quiet')
        finish(run,report(),empty);self.assertEqual(run['status'],'ready')
        blocked=report(findings=[],blockers=['Mail needs authorization.'])
        finish(run,blocked,{});self.assertEqual(run['status'],'ready')
        finish(run,blocked,blocked);self.assertEqual(run['status'],'quiet')
        self.assertEqual(run['report']['blockers'],blocked['blockers'])

    def test_report_requires_bounded_sources_and_unique_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            recorder=AssignmentReport(Path(tmp))
            with self.assertRaises(ValueError):recorder.read()
            invalid=report();invalid['findings'][0]['source']=''
            with self.assertRaises(ValueError):recorder.record(**invalid)
            invalid=report();invalid['findings']*=2
            with self.assertRaises(ValueError):recorder.record(**invalid)
            recorder.record(**report());self.assertEqual(recorder.read(),report())
            from capo.research_tools import ReadTools
            from capo.request_outcomes import assess
            import json
            tools=ReadTools([recorder.tool()])
            with self.assertRaises(ValueError):tools.call('monitor.report',report())
            saved=tools.call('monitor.report',report(),operation_id='report')
            result=assess(json.dumps({'outcomes':[{'requirement':'Save monitoring report', 'kind':'action',
                'status':'complete', 'evidence':['0'], 'next_step':''}]}),
                [{'tool':'monitor.report','result':saved}],tools)
            self.assertEqual(result['status'],'reported_complete')

    def test_unsent_and_other_owner_reports_do_not_suppress(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=DigestStore(Path(tmp));now=datetime.now(timezone.utc)
            try:
                base=dict(scope='owner',day='2030-01-01',schedule_id='s',revision=1,created=1,report=report())
                db.save(dict(base,key='unsent',status='ready'),now)
                db.save(dict(base,key='other',scope='other',status='sent'),now)
                self.assertEqual(previous_report(db,'owner','s',1,5),{})
                db.save(dict(base,key='sent',status='sent'),now)
                self.assertEqual(previous_report(db,'owner','s',1,5),report())
                self.assertEqual(previous_report(db,'owner','s',2,5),{})
            finally:db.close()

    def test_team_status_owner_isolation_paused_and_no_assignment(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp);config=dict(team_id='T',channel_id='C',owner_user_id='U')
            schedule=Schedules(home,owner_key(config)).save('','',fields(),'save')['schedule']
            other=dict(config,owner_user_id='OTHER')
            Schedules(home,owner_key(other)).save('','',fields(title='Other private assignment'),'other')
            db=DigestStore(home/'scheduled');now=datetime.now(timezone.utc)
            db.save(dict(key='run',scope=scope(config),day='2030-01-01',schedule_id=schedule['id'],revision=1,
                         status='quiet',created=now.timestamp(),checked_at=now.isoformat(),report=report(findings=[])),now)
            db.close()
            tools=shared_tools(home,config,Documents(home,owner_key(config)))
            status=tools.call('team.status',{})
            money=next(a for a in status['agents'] if a['agent']=='money_saver')
            self.assertEqual(len(money['assignments']),1)
            self.assertEqual(money['assignments'][0]['last_status'],'quiet')
            self.assertTrue(money['assignments'][0]['last_checked'])
            self.assertEqual(next(a for a in status['agents'] if a['agent']=='style_assistant')['assignments'],[])
            self.assertNotIn('Other private',str(status))

    def test_real_scheduler_reports_quiet_changed_and_restarts(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp);config=dict(team_id='T',channel_id='C',owner_user_id='U')
            Schedules(home,owner_key(config)).save('','',fields(),'save')
            client=Mock();client.chat_postMessage.return_value={'ts':'synthetic'}
            service=SimpleNamespace(store=SimpleNamespace(home=home),config=config,client=client,bot_user_id='BOT')
            current=report();observed=[]
            def generate(provider,tools,request,*args,**kwargs):
                self.assertTrue(all(not t.mutates for name,t in tools.tools.items() if name!='monitor.report'))
                self.assertNotIn('schedules.save',tools.tools)
                self.assertNotIn('tasks.save',tools.tools)
                observed.append(request)
                tools.call('monitor.report',current,operation_id='local-report')
                return {'reply':'Checked.','document':'','document_title':'',
                        'receipts':[{'tool':'mail.search','result':{'messages':['synthetic']}}]}
            now=datetime.fromisoformat('2030-01-01T10:00:00-08:00')
            with patch('capo.scheduled_requests.research',side_effect=generate):
                for day in range(3):
                    manager=ScheduledManager(service)
                    try:
                        if day==2:current=report('changed-price')
                        date=now+timedelta(days=day)
                        manager.tick(date)
                        for worker in manager.workers.values():worker.join(5);self.assertFalse(worker.is_alive())
                        manager.tick(date+timedelta(seconds=30))
                        manager.tick(date+timedelta(seconds=45))
                    finally:manager.db.close()
                self.assertEqual(client.chat_postMessage.call_count,2)
                self.assertEqual(observed[0]['agent'],'money_saver')
                self.assertEqual(observed[1]['previous_check'],report())

    def test_missing_report_is_a_blocker_not_clean_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp);config=dict(team_id='T',channel_id='C',owner_user_id='U')
            Schedules(home,owner_key(config)).save('','',fields(),'save')
            service=SimpleNamespace(store=SimpleNamespace(home=home),config=config,client=Mock(),bot_user_id='BOT')
            manager=ScheduledManager(service)
            result=dict(reply='Here is your report.',document='',document_title='',receipts=[])
            try:
                with patch('capo.scheduled_requests.research',return_value=result):
                    manager.tick(datetime.fromisoformat('2030-01-01T10:00:00-08:00'))
                    for worker in manager.workers.values():worker.join(5)
                row=manager.db.db.execute('SELECT data FROM runs').fetchone()
                import json
                run=json.loads(row[0])
                self.assertEqual(run['status'],'ready')
                self.assertIn('verified monitoring report',run['report']['blockers'][0])
            finally:manager.db.close()


    def test_post_report_provider_failure_preserves_findings_as_incomplete(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp);config=dict(team_id='T',channel_id='C',owner_user_id='U')
            Schedules(home,owner_key(config)).save('','',fields(),'save')
            service=SimpleNamespace(store=SimpleNamespace(home=home),config=config,client=Mock(),bot_user_id='BOT')
            manager=ScheduledManager(service)
            def generate(provider,tools,*args,**kwargs):
                tools.call('monitor.report',report(),operation_id='saved-before-failure')
                raise RuntimeError('synthetic provider secret must not appear')
            try:
                with patch('capo.scheduled_requests.research',side_effect=generate):
                    manager.tick(datetime.fromisoformat('2030-01-01T10:00:00-08:00'))
                    for worker in manager.workers.values():worker.join(5)
                import json
                run=json.loads(manager.db.db.execute('SELECT data FROM runs').fetchone()[0])
                self.assertEqual(run['status'],'ready')
                self.assertEqual(run['report']['findings'],report()['findings'])
                self.assertIn('could not finish verification',run['report']['blockers'][0])
                self.assertNotIn('synthetic provider secret',json.dumps(run))
                status=TeamStatus(home,config).status()
                money=next(a for a in status['agents'] if a['agent']=='money_saver')
                self.assertEqual(money['assignments'][0]['findings'],report()['findings'])
            finally:manager.db.close()
