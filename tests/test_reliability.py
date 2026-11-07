import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from capo.reliability import Reliability
from capo.capabilities import owner_key,shared_tools,Documents
from capo.request_timing import RequestTiming
from capo.store import Store

CONFIG={'team_id':'T1','channel_id':'C1','owner_user_id':'U1'}


class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.home=Path(tmp.name)
        self.reporter=Reliability(self.home,CONFIG);self.now=2000000000

    def timing(self,event,received):
        clock=RequestTiming(self.home,event)
        for phase,at in [('received',received),('execution_started',received+2),('first_reply',received+4),('completed',received+10),('delivered',received+12)]:clock.mark(phase,at=at)
        return clock

    def test_private_metadata_and_replay_counts_preserve_failure_evidence(self):
        self.timing('one',self.now-100)
        result={'receipts':[{'tool':'calendar.patch','error':'PRIVATE_SECRET_MAIL_BODY'}],
                'outcome_report':{'outcomes':[{'status':'partial'}]}}
        self.reporter.capture('one','partial',result,'100.123',at=self.now-90)
        self.reporter.capture('one','complete',at=self.now)
        report=self.reporter.report(7,now=self.now)
        self.assertEqual(report['requests'],1);self.assertEqual(report['outcomes'],{'partial':1})
        self.assertEqual(report['incident_requests'],1)
        self.assertEqual(report['latency']['metrics']['end_to_end']['median_seconds'],12)
        self.assertIn('calendar.patch',report['text']);self.assertIn('app.slack.com',report['text'])
        self.assertNotIn('PRIVATE_SECRET_MAIL_BODY',self.reporter.path.read_bytes().decode(errors='ignore'))
        self.assertEqual(self.reporter.path.stat().st_mode & 0o777,0o600)
        self.assertEqual(self.reporter.root.stat().st_mode & 0o777,0o700)

    def test_owner_scope_filters_counts_and_latency(self):
        self.timing('one',self.now-100);self.timing('other',self.now-100)
        self.reporter.capture('one','reported_complete',at=self.now-90)
        other=Reliability(self.home,dict(CONFIG,owner_user_id='U2'))
        other.capture('other','failed',at=self.now-90)
        value=self.reporter.report(7,now=self.now)
        self.assertEqual(value['outcomes'],{'reported_complete':1})
        self.assertEqual(value['latency']['requests'],1)
        self.assertEqual(value['incident_requests'],0)
        self.assertNotIn('other',value['text'])

    def test_time_window_and_unassessed_questions_do_not_invent_quality_grades(self):
        self.reporter.capture('old','failed',at=self.now-8*86400)
        self.reporter.capture('future','failed',at=self.now+100)
        self.reporter.capture('clarify','partial',{'outcome_report':{'outcomes':[{'status':'needs_input'}]}},at=self.now-10)
        report=self.reporter.report(7,now=self.now)
        self.assertEqual(report['requests'],1);self.assertEqual(report['needs_input_requests'],1)
        self.assertIsNone(report['unnecessary_clarifications']);self.assertIsNone(report['semantic_success_rate'])
        self.assertEqual(report['latency']['metrics'],{})
        self.assertIn('No complete timing samples',report['text'])
        with self.assertRaises(ValueError):self.reporter.report(0)

    def test_backfill_owner_only_preserves_pending_and_unknown_legacy_outcomes(self):
        store=Store(self.home)
        try:
            for eid,handled,user in [('pending',False,'U1'),('legacy',True,'U1'),('foreign',True,'U2')]:
                store.enqueue_slack(eid,{'team_id':'T1','event':{'user':user,'channel':'C1','ts':str(self.now-100)}})
                if handled:store.finish_slack(eid)
        finally:store.db.close()
        value=self.reporter.report(7,now=self.now)
        self.assertEqual(value['requests'],2);self.assertEqual(value['outcomes'],{'unknown':1,'pending':1})
        self.assertEqual(value['incident_requests'],0)

    def test_recorded_delivery_failure_and_partial_work_are_backfilled(self):
        store=Store(self.home)
        try:
            store.enqueue_slack('failed',{'team_id':'T1','event':{'user':'U1','channel':'C1','ts':str(self.now-100)}})
            store.finish_slack('failed')
            with store.db:
                store.db.execute('CREATE TABLE slack_deliveries(event_id TEXT PRIMARY KEY,data TEXT)')
                store.db.execute('INSERT INTO slack_deliveries VALUES(?,?)',('failed',json.dumps({'failed':True,'text':'PRIVATE FAILURE TEXT'})))
        finally:store.db.close()
        value=self.reporter.report(7,now=self.now)
        self.assertEqual(value['outcomes'],{'failed':1})
        self.assertNotIn('PRIVATE FAILURE TEXT',str(value))
        self.assertTrue((self.reporter.root/'latest-scorecard.json').exists())

    def test_shared_report_composes_with_private_incident_review(self):
        self.reporter.capture('one','failed',at=1)
        tools=shared_tools(self.home,CONFIG,Documents(self.home,owner_key(CONFIG)))
        with patch('capo.reliability.time.time',return_value=100):
            value=tools.call('reliability.report',{'days':'7'})
            incidents=tools.call('reliability.incidents',{'days':'7','cursor':''})
        self.assertEqual(value['incident_requests'],1)
        self.assertEqual(incidents['incidents'][0]['status'],'failed')
        self.assertFalse(tools.tools['reliability.report'].mutates)

    def test_failure_before_reasoning_is_captured_by_delivery_boundary(self):
        from capo.slack import SlackService
        store=Store(self.home);self.addCleanup(store.db.close)
        body={'team_id':'T1','event':{'type':'app_mention','user':'U1','channel':'C1','ts':'100.001','text':'Help'}}
        store.enqueue_slack('early-failure',body)
        client=Mock();client.chat_postMessage.return_value={'ok':True,'ts':'200.001'}
        config=dict(CONFIG,repositories={'fixture':{'path':str(self.home),'checks':['python3 -V']}})
        service=SlackService(store,config,client);service.bot_user_id='BOT'
        service.dispatch=Mock(side_effect=ValueError('Synthetic routing failure'))
        service.process_messages()
        value=self.reporter.report(7)
        self.assertEqual(value['outcomes'],{'failed':1})
        self.assertEqual(value['incident_requests'],1)

    def test_automations_are_reported_separately_and_private_failures_are_reviewable(self):
        from datetime import datetime,timezone
        from capo.digest import DigestStore,scope
        db=DigestStore(self.home/'scheduled')
        try:
            for key,status,owner in [('failed','expired',CONFIG),('clean','quiet',CONFIG),('other','failed',dict(CONFIG,owner_user_id='U2'))]:
                run={'key':key,'scope':scope(owner),'status':status,'created':self.now-100,'day':'synthetic',
                     'request':'PRIVATE AUTOMATION PROMPT','error_summary':'PRIVATE PROVIDER ERROR'}
                db.save(run,datetime.fromtimestamp(self.now-50,timezone.utc))
        finally:db.close()
        value=self.reporter.report(7,now=self.now)
        self.assertEqual(value['requests'],0)
        self.assertEqual(value['automation_runs']['count'],2)
        self.assertEqual(value['automation_runs']['incident_runs'],1)
        self.assertNotIn('PRIVATE',str(value))
        with patch('capo.reliability.time.time',return_value=self.now):
            incidents=self.reporter.incidents(7,'')['incidents']
        self.assertEqual(incidents[0]['kind'],'automation')
        self.assertEqual(incidents[0]['status'],'expired')
        self.assertEqual(incidents[0]['source_id'],'failed')

    def test_recurring_signals_require_separate_incident_observations(self):
        for event in ('one','two'):
            self.reporter.capture(event,'failed',{'receipts':[{'tool':'web.search','error':'private'}]},at=self.now-10)
        result=self.reporter.report(7,now=self.now)
        signals={r['signal']:r['observations'] for r in result['recurring_signals']}
        self.assertEqual(signals['tool error: web.search'],2)

    def test_completed_execution_is_not_mistaken_for_a_delivered_reply(self):
        store=Store(self.home)
        try:
            store.enqueue_slack('complete-undelivered',{'team_id':'T1','event':{'user':'U1','channel':'C1','ts':str(self.now-100)}})
            self.reporter.capture('complete-undelivered','reported_complete',at=self.now-100)
            report=self.reporter.report(7,now=self.now)
            self.assertEqual(report['outcomes'],{'reported_complete':1})
            self.assertEqual(report['replies_awaiting_delivery'],1)
            self.assertIn('Replies awaiting delivery: 1',report['text'])
            store.finish_slack('complete-undelivered')
            self.assertEqual(self.reporter.report(7,now=self.now)['replies_awaiting_delivery'],0)
        finally:store.db.close()
