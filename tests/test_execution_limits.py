import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch

from capo.contracts import object_schema
from capo.execution_limits import settings
from capo.recovery import RetryLater
from capo.research_tools import ReadTool,ReadTools,research


STEP=dict(action='tool',tool='items.read',arguments_json='{}',reply='',document_title='',document='')
FINISH=dict(action='finish',tool='',arguments_json='{}',reply='Done.',document_title='',document='')


class ExecutionLimitTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name)
        self.execute=Mock(return_value={'items':[]})
        self.tools=ReadTools([ReadTool('items.read','Read items',object_schema({}),self.execute)])
        self.request={'message':'Inspect the requested items'}

    def test_settings_reject_invalid_limits(self):
        self.assertEqual(settings(),{'max_calls':10,'evidence_chars':180000})
        for value in ([],{'other':2},{'max_calls':True},{'max_calls':41},{'max_calls':0},
                      {'evidence_chars':999},{'evidence_chars':360001}):
            with self.subTest(value=value),self.assertRaises(ValueError):settings(value)

    def test_configured_tool_limit_stops_and_caches_partial_result(self):
        backend=Mock();backend.call.return_value=STEP
        result=research(backend,self.tools,self.request,self.root,limits={'max_calls':2})
        self.assertEqual(self.execute.call_count,2)
        self.assertEqual(backend.call.call_count,3)
        self.assertEqual(result['stop_reason'],'tool_limit')
        self.assertEqual(research(backend,self.tools,self.request,self.root,limits={'max_calls':40}),result)
        self.assertEqual(backend.call.call_count,3)

    def test_budget_pinned_across_provider_retry_and_config_change(self):
        backend=Mock();backend.call.side_effect=[TimeoutError(),STEP,STEP]
        with patch('capo.recovery.time.time',return_value=100),self.assertRaises(RetryLater):
            research(backend,self.tools,self.request,self.root,limits={'max_calls':1})
        with patch('capo.recovery.time.time',return_value=200):
            result=research(backend,self.tools,self.request,self.root,limits={'max_calls':20})
        self.execute.assert_called_once()
        self.assertEqual(result['stop_reason'],'tool_limit')
        self.assertEqual(backend.call.call_args_list[0].args[1],backend.call.call_args_list[1].args[1])

    def test_legacy_request_keeps_original_budget(self):
        backend=Mock();backend.call.side_effect=[TimeoutError(),STEP,STEP,STEP]
        with patch('capo.recovery.time.time',return_value=100),self.assertRaises(RetryLater):
            research(backend,self.tools,self.request,self.root,max_calls=2)
        with patch('capo.recovery.time.time',return_value=200):
            research(backend,self.tools,self.request,self.root,limits={'max_calls':20,'evidence_chars':1000})
        self.assertEqual(self.execute.call_count,2)
        state=json.loads((self.root/'checkpoint.json').read_text())
        self.assertEqual(state['execution_limits'],{'max_calls':2,'evidence_chars':180000})

    def test_oversized_write_result_is_preserved_without_another_write(self):
        result={'saved':True,'id':'item','details':'x'*2000}
        self.execute.return_value=result
        tools=ReadTools([ReadTool('items.read','Create requested item',object_schema({}),self.execute,True)])
        backend=Mock();backend.call.return_value=STEP
        output=research(backend,tools,self.request,self.root,limits={'evidence_chars':1000})
        self.execute.assert_called_once()

        self.assertEqual(output['stop_reason'],'evidence_limit')
        receipt=output['receipts'][0]
        self.assertNotIn('error',receipt)
        self.assertTrue(receipt['result']['truncated'])
        self.assertEqual(json.loads((self.root/receipt['result']['artifact']).read_text()),result)
        self.assertEqual(research(backend,tools,self.request,self.root,limits={'evidence_chars':360000}),output)
        self.execute.assert_called_once()

    def test_evidence_exhaustion_survives_interrupted_final_answer(self):
        self.execute.return_value={'large':'x'*2000}
        backend=Mock();backend.call.side_effect=[STEP,TimeoutError(),STEP]
        with patch('capo.recovery.time.time',return_value=100),self.assertRaises(RetryLater):
            research(backend,self.tools,self.request,self.root,limits={'evidence_chars':1000})
        with patch('capo.recovery.time.time',return_value=200):
            result=research(backend,self.tools,self.request,self.root,limits={'evidence_chars':360000})
        self.execute.assert_called_once()
        self.assertEqual(result['stop_reason'],'evidence_limit')
