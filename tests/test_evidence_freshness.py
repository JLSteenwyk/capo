import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch

from capo.contracts import object_schema
from capo.evidence_freshness import freshness
from capo.recovery import RetryLater
from capo.research_tools import ReadTool,ReadTools,research
from capo.request_outcomes import assess,OutcomeError


def step(tool='',index='0',reply='Current state checked.'):
    return dict(action='tool' if tool else 'finish',tool=tool,
        arguments_json='{}' if tool else json.dumps({'outcomes':[dict(requirement='Check current state',kind='answer',status='complete',evidence=[index],next_step='')]}),
        reply='' if tool else reply,document_title='',document='')


class EvidenceFreshnessTests(unittest.TestCase):
    def test_delayed_restart_refreshes_read_without_repeating_prior_write(self):
        current=Mock(side_effect=[{'state':'unanswered'},{'state':'answered'}])
        tools=ReadTools([ReadTool('source.current','Read current thread',object_schema({}),current,fresh_for=300)])
        backend=Mock();backend.call.side_effect=[step('source.current'),TimeoutError(),step(),step('source.current'),step(index='2',reply='Already answered.')]
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp)
            with patch('time.time',return_value=100):
                with self.assertRaises(RetryLater):research(backend,tools,{},directory,max_calls=8)
            with patch('time.time',return_value=500):
                result=research(backend,tools,{},directory,max_calls=8)
        self.assertEqual(result['status'],'reported_complete')
        self.assertEqual(current.call_count,2)
        self.assertIn('stale',result['receipts'][1]['error'])
        self.assertEqual(result['reply'],'Already answered.')
        prompt=json.loads(backend.call.call_args.args[1].splitlines()[-1])
        self.assertEqual(prompt['receipts'][0]['freshness'],'stale')
        self.assertEqual(prompt['receipts'][2]['freshness'],'fresh')

    def test_unknown_future_and_expired_timestamps_do_not_prove_current_state(self):
        tools=ReadTools([ReadTool('source.current','Read',object_schema({}),Mock(),fresh_for=300)])
        for stamp in (None,100,1000,float('nan')):
            receipt={'tool':'source.current','result':{},'observed_at':stamp}
            with patch('time.time',return_value=500),self.assertRaises(OutcomeError):
                assess(step()['arguments_json'],[receipt],tools)
        receipt={'tool':'source.current','result':{},'observed_at':300}
        with patch('time.time',return_value=500):
            self.assertEqual(assess(step()['arguments_json'],[receipt],tools)['status'],'reported_complete')

    def test_historical_mutations_and_immutable_sources_do_not_expire(self):
        tools=ReadTools([ReadTool('items.save','Save',object_schema({}),Mock(),mutates=True,fresh_for=5),
                        ReadTool('document.read','Read historical source',object_schema({}),Mock())])
        for name in tools.tools:
            self.assertEqual(freshness({'tool':name,'observed_at':1},tools,1000),'historical')

    def test_live_registry_tags_current_task_mail_and_calendar_reads(self):
        from capo.capabilities import shared_tools,Documents
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp);tools=shared_tools(home,{'calendar':{'enabled':True},'gmail':{'enabled':True,'drafts':True}},Documents(home,'owner'))
            for name in ('tasks.overview','tasks.refresh','tasks.search','tasks.get','mail.search','mail.thread',
                         'mail.drafts.read','calendar.events','calendar.event','calendar.availability'):
                self.assertGreater(tools.tools[name].fresh_for,0,name)
            self.assertEqual(tools.tools['documents.read'].fresh_for,0)

    def test_scheduled_clean_claim_requires_a_real_source_attempt(self):
        backend=Mock();backend.call.return_value=step()
        source=Mock();tools=ReadTools([ReadTool('source.current','Read',object_schema({}),source,fresh_for=300)])
        with tempfile.TemporaryDirectory() as tmp:
            result=research(backend,tools,{},Path(tmp),require_source_inspection=True)
        self.assertEqual(result['status'],'partial')
        self.assertEqual(result['stop_reason'],'source_inspection_missing')
        source.assert_not_called()
