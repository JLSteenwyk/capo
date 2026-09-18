import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from capo.capacity import Capacity
from capo.contracts import TEXT, object_schema
from capo.research_tools import ReadTool, ReadTools, research
from capo.worker_delegation import WorkerTools, bind
from capo.recovery import RecoveryStopped


def finish(text='Compared the supplied options; A is less expensive.'):
    return dict(action='finish',tool='',arguments_json=json.dumps({'outcomes':[{'requirement':'Compare options','kind':'answer','status':'complete','evidence':[],'next_step':''}]}),reply=text,document='',document_title='')


def step(name, arguments):
    return dict(action='tool',tool=name,arguments_json=json.dumps(arguments),reply='',document='',document_title='')


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.home=Path(self.temp.name);self.directory=self.home/'request';self.directory.mkdir()
        self.workers=WorkerTools(self.home,'synthetic-owner')
        self.provider=Mock()
        self.provider.capacity=Capacity(self.home,clock=lambda:1800000000,probes={
            p:lambda used=used:[dict(name='primary',used_percent=used,reset_at=1900000000)] for p,used in [('grok',0),('codex',90)]})
        self.provider.call.return_value=finish()
        self.web=Mock(return_value={'url':'https://example.com','text':'A costs 10; B costs 20'})
        self.mutation=Mock()
        self.tools=ReadTools(self.workers.tools()+[
            ReadTool('web.read','Read a page',object_schema({'url':TEXT}),self.web),
            ReadTool('calendar.create','Create event',object_schema({}),self.mutation,mutates=True),
            ReadTool('mail.search','Search private messages',object_schema({}),Mock())])
        self.bound=bind(self.tools,self.provider,self.directory,{'timezone':'UTC'},None)
        self.args=dict(objective='Compare A and B',context='A costs 10, B costs 20',acceptance_criteria='Identify the cheaper option',provider='auto')

    def test_spare_grok_capacity_is_used_and_actual_receipt_is_saved(self):
        result=self.bound.call('workers.delegate',self.args)
        self.assertEqual(result['provider'],'grok')
        self.assertEqual(result['status'],'reported_complete')
        self.assertTrue(result['chief_review_required'])
        self.assertEqual(self.provider.call.call_args.args[0],'grok')
        row=self.workers.activity()['assignments'][0]
        self.assertEqual(row['native_calls'],1)
        self.assertEqual(row['status'],'reported_complete')
        self.bound.call('workers.delegate',self.args)
        self.assertEqual(self.provider.call.call_count,1)

    def test_worker_composes_only_inherited_read_tools(self):
        self.provider.call.side_effect=[step('web.read',{'url':'https://example.com'}),finish()]
        result=self.bound.call('workers.delegate',self.args)
        self.assertEqual(result['receipts'][0]['tool'],'web.read')
        prompt=self.provider.call.call_args_list[0].args[1]
        catalog=json.loads(prompt.splitlines()[-1])['tools']
        self.assertEqual([t['name'] for t in catalog],['web.read'])
        self.mutation.assert_not_called()

    def test_schedule_prefix_filter_cannot_be_bypassed(self):
        narrowed=ReadTools(self.workers.tools())
        bound=bind(narrowed,self.provider,self.directory,{},None)
        self.provider.call.side_effect=[step('web.read',{'url':'https://example.com'}),finish()]
        result=bound.call('workers.delegate',self.args)
        self.web.assert_not_called()
        self.assertIn('error',result['receipts'][0])

    def test_explicit_provider_is_pinned_despite_lower_capacity(self):
        result=self.bound.call('workers.delegate',dict(self.args,provider='codex'))
        self.assertEqual(result['provider'],'codex')
        self.assertEqual(self.provider.call.call_args.args[0],'codex')

    def test_two_assignment_limit_is_durable_across_rebinding(self):
        for name in ('First','Second'):
            self.bound.call('workers.delegate',dict(self.args,objective=name))
        bound=bind(self.tools,self.provider,self.directory,{},None)
        result=bound.call('workers.delegate',dict(self.args,objective='Third'))
        self.assertEqual(result['status'],'blocked')
        self.assertEqual(self.provider.call.call_count,2)

    def test_failed_worker_is_reported_without_repeating_or_leaking_errors(self):
        self.provider.call.side_effect=ValueError('private provider diagnostic')
        result=self.bound.call('workers.delegate',self.args)
        self.assertEqual(result['status'],'failed')
        self.assertNotIn('private provider',json.dumps(result))
        self.bound.call('workers.delegate',self.args)
        self.assertEqual(self.provider.call.call_count,1)

    def test_cancellation_and_recursive_delegation_are_blocked(self):
        (self.directory/'cancelled.json').touch()
        with self.assertRaises(RecoveryStopped):self.bound.call('workers.delegate',self.args)
        self.provider.call.assert_not_called()
        with self.assertRaises(ValueError):research(self.provider,self.tools,{},self.home/'recursive',reasoning_provider='grok')

    def test_recent_failures_affect_auto_routing_but_not_explicit_choice(self):
        self.workers.root.mkdir(parents=True)
        for i in range(2):(self.workers.root/f'{i}.json').write_text(json.dumps({'provider':'grok','status':'failed'}))
        self.provider.capacity=Capacity(self.home/'other',clock=lambda:1800000000,probes={
            p:lambda:[dict(name='primary',used_percent=0,reset_at=1900000000)] for p in ('grok','codex')})
        self.bound=bind(self.tools,self.provider,self.directory,{},None)
        self.assertEqual(self.bound.call('workers.delegate',self.args)['provider'],'codex')

    def test_chief_delegates_and_reviews_in_same_loop(self):
        self.provider.call.side_effect=[step('workers.delegate',self.args),finish('Worker comparison: A costs less.'),finish('I reviewed the comparison: A costs less.')]
        result=research(self.provider,self.tools,{'message':'Compare options'},self.directory)
        self.assertEqual([c.args[0] for c in self.provider.call.call_args_list],['claude','grok','claude'])
        self.assertEqual(result['receipts'][0]['result']['provider'],'grok')
        self.assertIn('reviewed',result['reply'])

    def test_exhausted_or_login_blocked_workers_are_not_called(self):
        self.provider.capacity=Mock()
        self.provider.capacity.snapshot.return_value={p:{'state':'unknown','probe_error':'login_required','remaining_percent':None} for p in ('grok','codex')}
        result=bind(self.tools,self.provider,self.directory,{},None).call('workers.delegate',self.args)
        self.assertEqual(result['status'],'blocked')
        self.provider.call.assert_not_called()

    def test_repeated_recent_failures_temporarily_avoid_worker(self):
        import time
        self.workers.root.mkdir(parents=True)
        for i in range(2):
            (self.workers.root/f'{i}.json').write_text(json.dumps({'provider':'grok','status':'failed','started_at':time.time()}))
        result=self.bound.call('workers.delegate',self.args)
        self.assertEqual(result['provider'],'codex')

    def test_worker_read_budget_is_enforced_without_recursive_expansion(self):
        self.provider.call.return_value=step('web.read',{'url':'https://example.com'})
        result=self.bound.call('workers.delegate',self.args)
        self.assertEqual(result['status'],'partial')
        self.assertEqual(self.web.call_count,3)
        self.assertEqual(self.provider.call.call_count,4)

    def test_delegated_report_proves_handoff_but_never_external_action(self):
        from capo.request_outcomes import assess, OutcomeError
        result=self.bound.call('workers.delegate',self.args)
        receipts=[{'tool':'workers.delegate','result':result}]
        item={'requirement':'Delegate comparison','kind':'handoff','status':'complete','evidence':['0'],'next_step':''}
        self.assertEqual(assess(json.dumps({'outcomes':[item]}),receipts,self.bound)['status'],'reported_complete')
        item['kind']='action'
        with self.assertRaises(OutcomeError):assess(json.dumps({'outcomes':[item]}),receipts,self.bound)
        item['kind']='handoff'
        receipts[0]['result']={'status':'failed','handoff_completed':False}
        with self.assertRaises(OutcomeError):assess(json.dumps({'outcomes':[item]}),receipts,self.bound)
