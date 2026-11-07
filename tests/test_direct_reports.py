import json
import tempfile
import unittest
from datetime import datetime,timezone,timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from capo.capabilities import owner_key
from capo.digest import scope
from capo.research_tools import ReadTool,ReadTools
from capo.contracts import object_schema
from capo.schedules import Schedules
from capo.scheduled_requests import ScheduledManager,direct_report
from capo.reliability import Reliability
from test_team_assignments import fields


class DirectReportTests(unittest.TestCase):
    def test_mutations_delegations_and_unavailable_tools_are_rejected_without_calls(self):
        callback=Mock(return_value={'text':'unsafe'})
        for flags in ({'mutates':True},{'handoff':True},{'verifies':True},{'finalizes':True}):
            tools=ReadTools([ReadTool('sample.report','Report',object_schema({}),callback,**flags)])
            with self.assertRaises(ValueError):direct_report(tools,'sample.report','{}')
        with self.assertRaises(ValueError):direct_report(ReadTools([]),'missing.report','{}')
        callback.assert_not_called()

    def test_arguments_and_output_pass_host_contract_checks(self):
        callback=Mock(return_value={'text':'A useful report.'})
        tools=ReadTools([ReadTool('sample.report','Report',object_schema({}),callback)])
        with self.assertRaises(ValueError):direct_report(tools,'sample.report','{"unexpected":"value"}')
        callback.assert_not_called()
        value=direct_report(tools,'sample.report','{}')
        self.assertEqual(value['reply'],'A useful report.')
        self.assertEqual(value['receipts'][0]['tool'],'sample.report')
        callback.return_value={'text':''}
        with self.assertRaises(ValueError):direct_report(tools,'sample.report','{}')

    def test_schedule_keeps_direct_settings_on_edit_and_requires_bounded_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Schedules(Path(tmp),'owner')
            options=fields(agent='capo',delivery='always',report_tool='reliability.report',report_arguments='{"days":"7"}')
            row=store.save('','',options,'create')['schedule']
            edited=store.save(row['id'],'1',fields(agent='capo',delivery='always',enabled=False),'pause')['schedule']
            self.assertEqual(edited['report_tool'],'reliability.report')
            self.assertEqual(edited['report_arguments'],'{"days":"7"}')
            for changes in ({'report_arguments':'[]'},{'delivery':'changes'},{'report_tool':'not registered'}):
                with self.assertRaises(ValueError):store.save('','',dict(options,**changes),json.dumps(changes))

    def test_scheduled_report_is_delivered_once_without_a_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp);config={'team_id':'T1','channel_id':'C1','owner_user_id':'U1'}
            now=datetime.now(timezone.utc).replace(second=0,microsecond=0)+timedelta(minutes=1)
            row=Schedules(home,owner_key(config)).save('','',fields(agent='capo',delivery='always',
                time=now.strftime('%H:%M'),timezone='UTC',tool_prefixes=['reliability.'],
                report_tool='reliability.report',report_arguments='{"days":"7"}'),'weekly')['schedule']
            Reliability(home,config).capture('failure','failed')
            client=Mock();client.chat_postMessage.return_value={'ok':True,'ts':'200.001'}
            service=SimpleNamespace(config=config,store=SimpleNamespace(home=home),client=client,bot_user_id='BOT')
            manager=ScheduledManager(service)
            try:
                with patch('capo.scheduled_requests.Providers',side_effect=AssertionError('Direct reports need no model')):
                    manager.tick(now)
                    for worker in manager.workers.values():worker.join(5)
                    manager.tick(now)
                    manager.tick(now)
                client.chat_postMessage.assert_called_once()
                self.assertIn('Failed: 1',client.chat_postMessage.call_args.kwargs['text'])
                records=manager.db.db.execute('SELECT status FROM runs WHERE scope=?',(scope(config),)).fetchall()
                self.assertEqual([r[0] for r in records],['sent'])
            finally:manager.db.close()

    def test_expired_unsent_snapshot_rebuilds_from_current_evidence_before_delivery(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp);config={'team_id':'T1','channel_id':'C1','owner_user_id':'U1'}
            now=datetime.now(timezone.utc).replace(second=0,microsecond=0)+timedelta(minutes=1)
            Schedules(home,owner_key(config)).save('','',fields(agent='capo',delivery='always',
                time=now.strftime('%H:%M'),timezone='UTC',tool_prefixes=['reliability.'],
                report_tool='reliability.report',report_arguments='{"days":"7"}'),'refresh')['schedule']
            reporter=Reliability(home,config);reporter.capture('first','failed')
            client=Mock();client.chat_postMessage.return_value={'ok':True,'ts':'300.001'}
            service=SimpleNamespace(config=config,store=SimpleNamespace(home=home),client=client,bot_user_id='BOT')
            manager=ScheduledManager(service)
            try:
                manager.tick(now)
                for worker in manager.workers.values():worker.join(5)
                client.chat_postMessage.assert_not_called()
                later=now+timedelta(minutes=6)
                reporter.capture('second','failed',at=later.timestamp()-1)
                with patch('time.time',return_value=later.timestamp()):
                    manager.tick(later)
                    for worker in manager.workers.values():worker.join(5)
                    manager.tick(later)
                client.chat_postMessage.assert_called_once()
                self.assertIn('Failed: 2',client.chat_postMessage.call_args.kwargs['text'])
                records=manager.db.db.execute('SELECT data FROM runs').fetchall()
                saved=json.loads(records[0][0])
                self.assertEqual(saved['attempts'],2)
                self.assertEqual(saved['status'],'sent')
            finally:manager.db.close()
