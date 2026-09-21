"""Background recovery must retain budgets, evidence and schedule authority."""
import json
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from capo.capabilities import owner_key
from capo.monitoring_progress import completion_gaps
from capo.recovery import RetryLater
from capo.research_tools import ReadTool, ReadTools, research
from capo.scheduled_requests import ScheduledManager
from capo.schedules import Schedules
from capo.contracts import object_schema


class BackgroundRecoveryTests(unittest.TestCase):
    def test_specific_partial_cause_is_not_reported_as_budget_exhaustion(self):
        result={'outcome_report':{'outcomes':[{'status':'partial','next_step':'One source returned permission denied.'}]}}
        self.assertEqual(completion_gaps(result),['One source returned permission denied.'])
        self.assertNotIn('limit', completion_gaps({'status':'partial'})[0])

    def test_temporary_failure_resumes_same_checkpoint_after_window_and_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp);config=dict(team_id='T',channel_id='C',owner_user_id='U')
            Schedules(home,owner_key(config)).save('','',dict(title='Source check',request='Inspect sources.',
                weekdays=list('0123456'),time='10:00',timezone='America/Los_Angeles',enabled=True,
                catch_up_hours='1',delivery='changes'),'schedule')
            service=SimpleNamespace(store=SimpleNamespace(home=home),config=config,client=Mock(),bot_user_id='BOT')
            start=datetime.fromisoformat('2030-01-01T10:00:00-08:00');paths=[]
            def generate(provider,tools,request,directory,**kwargs):
                self.assertEqual(kwargs['max_calls'],10)
                paths.append(directory)
                if len(paths)==1:raise RetryLater((start+timedelta(minutes=65)).timestamp())
                tools.call('monitor.report',dict(findings=[],blockers=[],coverage='Sources inspected.'),'report')
                return dict(reply='Checked.',document='',document_title='',status='complete',
                            receipts=[dict(tool='mail.read',result={'messages':[]})])
            with patch('capo.scheduled_requests.shared_tools',return_value=ReadTools([])), \
                 patch('capo.scheduled_requests.research',side_effect=generate):
                for minute in [0,65]:
                    manager=ScheduledManager(service)
                    manager.tick(start+timedelta(minutes=minute))
                    for worker in manager.workers.values():
                        worker.join(5);self.assertFalse(worker.is_alive())
                    rows=manager.db.db.execute('SELECT data FROM runs').fetchall()
                    self.assertEqual(len(rows),1)
                    run=json.loads(rows[0][0])
                    self.assertEqual(run['deadline'],(start+timedelta(minutes=90)).timestamp())
                    self.assertEqual(run['status'],'queued' if minute==0 else 'quiet')
                    manager.db.close()
            self.assertEqual(paths[0],paths[1])
            service.client.chat_postMessage.assert_not_called()

    def test_recovery_window_is_finite_and_paused_schedule_does_not_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp);config=dict(team_id='T',channel_id='C',owner_user_id='U')
            schedules=Schedules(home,owner_key(config))
            fields=dict(title='Source check',request='Inspect sources.',weekdays=list('0123456'),time='10:00',
                        timezone='America/Los_Angeles',enabled=True,catch_up_hours='1')
            schedule=schedules.save('','',fields,'schedule')['schedule']
            service=SimpleNamespace(store=SimpleNamespace(home=home),config=config,client=Mock(),bot_user_id='BOT')
            start=datetime.fromisoformat('2030-01-01T10:00:00-08:00')
            with patch('capo.scheduled_requests.shared_tools',return_value=ReadTools([])), \
                 patch('capo.scheduled_requests.research',side_effect=RetryLater((start+timedelta(minutes=65)).timestamp())) as generate:
                manager=ScheduledManager(service);manager.tick(start)
                for worker in manager.workers.values():worker.join(5)
                schedules.save(schedule['id'],str(schedule['revision']),dict(fields,enabled=False),'pause')
                manager.tick(start+timedelta(minutes=65))
                self.assertEqual(generate.call_count,1)
                manager.tick(start+timedelta(minutes=91))
                run=json.loads(manager.db.db.execute('SELECT data FROM runs').fetchone()[0])
                self.assertEqual(run['status'],'expired')
                manager.db.close()

    def test_wall_clock_expiration_stops_worker_even_if_monotonic_clock_did_not_advance(self):
        from capo.process import run_process
        with tempfile.TemporaryDirectory() as tmp:
            worker=Mock(pid=12345,returncode=0)
            with patch('capo.process.subprocess.Popen',return_value=worker), \
                 patch('capo.process.time.time',side_effect=[0,1000]), \
                 patch('capo.process.time.monotonic',return_value=0), \
                 patch('capo.process.os.killpg'),patch('capo.process.os.write'),patch('capo.process.reconcile_local'):
                with self.assertRaises(subprocess.TimeoutExpired):
                    run_process(['synthetic'],Path(tmp),Path(tmp)/'attempt',90)
            worker.communicate.assert_not_called()
            worker.wait.assert_called_once_with(timeout=8)

    def test_last_calls_are_reserved_for_supported_updates_without_requiring_a_write(self):
        def step(tool):return dict(action='tool',tool=tool,arguments_json='{}',reply='',document='',document_title='')
        done=dict(action='finish',tool='',arguments_json=json.dumps({'outcomes':[dict(requirement='Inspect',kind='answer',status='complete',evidence=['0'],next_step='')]}),
                  reply='No update needed.',document='',document_title='')
        read=Mock(return_value={'checked':True});write=Mock(return_value={'saved':True})
        tools=ReadTools([ReadTool('source.read','Read',object_schema({}),read),
                         ReadTool('record.save','Save supported changes',object_schema({}),write,mutates=True,settles=True)])
        provider=Mock();provider.call.side_effect=[step('source.read'),step('source.read'),done]
        with tempfile.TemporaryDirectory() as tmp:
            result=research(provider,tools,{},Path(tmp),max_calls=3)
        self.assertEqual(read.call_count,1);write.assert_not_called()
        self.assertIn('unavailable',result['receipts'][1]['error'])
        self.assertEqual(result['status'],'reported_complete')

    def test_settlement_can_save_within_original_budget(self):
        def step(tool):return dict(action='tool',tool=tool,arguments_json='{}',reply='',document='',document_title='')
        done=dict(action='finish',tool='',arguments_json=json.dumps({'outcomes':[dict(requirement='Save update',kind='action',status='complete',evidence=['1'],next_step='')]}),
                  reply='Update saved.',document='',document_title='')
        write=Mock(return_value={'saved':True})
        tools=ReadTools([ReadTool('source.read','Read',object_schema({}),lambda:{'checked':True}),
                         ReadTool('record.save','Save',object_schema({}),write,mutates=True,settles=True)])
        provider=Mock();provider.call.side_effect=[step('source.read'),step('record.save'),done]
        with tempfile.TemporaryDirectory() as tmp:
            result=research(provider,tools,{},Path(tmp),max_calls=3)
        write.assert_called_once()
        self.assertTrue(write.call_args.kwargs['operation_id'])
        self.assertEqual(result['status'],'reported_complete')
        self.assertEqual(len(result['receipts']),2)

    def test_monitor_only_acknowledges_the_batch_it_completed(self):
        from capo.monitoring import Monitor
        with tempfile.TemporaryDirectory() as tmp:
            monitor=Monitor(Path(tmp),{})
            items=[dict(id='a',kind='email',message_id='a',snippet='Source A'),
                   dict(id='b',kind='email',message_id='b',snippet='Source B')]
            with patch('capo.monitoring.research',return_value={'status':'reported_complete'}) as generate:
                monitor.tick(datetime.now().astimezone(),items)
                self.assertEqual(len(generate.call_args.args[2]['observations']),1)
                self.assertEqual(len(monitor.observations.changed(items)),1)
                monitor.tick(datetime.now().astimezone(),items)
                self.assertEqual(len(monitor.observations.changed(items)),0)

    def test_provider_deadline_prevents_new_calls_and_caps_remaining_attempt(self):
        from capo.providers import Providers
        provider=Providers(timeout=90,config={},capacity=Mock(),deadline=100)
        with patch('time.time',return_value=95):
            with patch.object(provider,'_call',side_effect=lambda *args:provider.timeout):
                self.assertEqual(provider.call('claude','',{},Path('.'),Path('.')),5)
        self.assertEqual(provider.timeout,90)
        with patch('time.time',return_value=101),patch.object(provider,'_call') as call:
            with self.assertRaises(TimeoutError):provider.call('claude','',{},Path('.'),Path('.'))
            call.assert_not_called()

    def test_supervisor_timeout_is_a_retryable_timeout_not_generic_failure(self):
        from capo.process import run_process
        with tempfile.TemporaryDirectory() as tmp:
            worker=Mock(pid=12345,returncode=124)
            with patch('capo.process.subprocess.Popen',return_value=worker), \
                 patch('capo.process.os.write'),patch('capo.process.reconcile_local'):
                with self.assertRaises(subprocess.TimeoutExpired):
                    run_process(['synthetic'],Path(tmp),Path(tmp)/'attempt',90)

    def test_routine_effort_is_explicit_and_does_not_enable_native_tools(self):
        from capo.providers import Providers
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with patch('capo.providers.run_cli',return_value=json.dumps({'structured_output':{'reply':'ready'}})) as cli:
                provider=Providers(config={},capacity=Mock(),effort='medium')
                provider.call('claude','Check',object_schema({'reply':{'type':'string'}}),root,root/'attempt')
                args=cli.call_args.args[1]
                self.assertEqual(args[args.index('--effort')+1],'medium')
                self.assertEqual(args[args.index('--tools')+1],'')
                self.assertEqual(args[args.index('--permission-mode')+1],'dontAsk')

    def test_malformed_arguments_can_be_repaired_without_repeating_a_write(self):
        write=Mock(return_value={'saved':True})
        tools=ReadTools([ReadTool('record.save','Save',object_schema({}),write,mutates=True)])
        def step(args):return dict(action='tool',tool='record.save',arguments_json=args,reply='',document='',document_title='')
        done=dict(action='finish',tool='',arguments_json=json.dumps({'outcomes':[dict(requirement='Save',kind='action',status='complete',evidence=['1'],next_step='')]}),reply='Saved.',document='',document_title='')
        provider=Mock();provider.call.side_effect=[step('{'),step('{}'),done]
        with tempfile.TemporaryDirectory() as tmp:
            result=research(provider,tools,{},Path(tmp),max_calls=3)
        write.assert_called_once()
        self.assertIn('Invalid JSON arguments',result['receipts'][0]['error'])
        self.assertIn('No tool was executed',result['receipts'][0]['error'])
        self.assertEqual(result['status'],'reported_complete')

    def test_service_json_error_does_not_claim_tool_was_never_called(self):
        read=Mock(side_effect=json.JSONDecodeError('Malformed response','',0))
        tools=ReadTools([ReadTool('source.read','Read',object_schema({}),read)])
        provider=Mock();provider.call.side_effect=[dict(action='tool',tool='source.read',arguments_json='{}',reply='',document='',document_title=''),
            dict(action='finish',tool='',arguments_json=json.dumps({'outcomes':[dict(requirement='Read',kind='answer',status='partial',evidence=['0'],next_step='Investigate the service response.')]}),reply='Source unavailable.',document='',document_title='')]
        with tempfile.TemporaryDirectory() as tmp:
            result=research(provider,tools,{},Path(tmp),max_calls=2)
        read.assert_called_once()
        self.assertNotIn('No tool was executed',result['receipts'][0]['error'])
        self.assertNotIn('Malformed response',result['receipts'][0]['error'])
