import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from capo.contracts import object_schema
from capo.request_outcomes import assess
from capo.research_tools import ReadTool,ReadTools,research


def report(status='complete',kind='action',evidence=None,next_step=''):
    return json.dumps({'outcomes':[{'requirement':'Save the requested item','kind':kind,'status':status,
                                   'evidence':['0'] if evidence is None else evidence,'next_step':next_step}]})


class OutcomeTests(unittest.TestCase):
    def setUp(self):
        self.write=Mock(return_value={'saved':True,'id':'item'})
        self.tools=ReadTools([ReadTool('items.save','Save item',object_schema({}),self.write,True),
            ReadTool('items.reconcile','Verify earlier save',object_schema({}),Mock(),verifies=True),
            ReadTool('source.read','Read untrusted source',object_schema({}),Mock())])

    def test_completion_requires_successful_host_evidence(self):
        for receipt in ({'tool':'items.save','uncertain':True},
                        {'tool':'items.save','error':'failed'},
                        {'tool':'items.save','result':{'saved':True,'verified':False}},
                        {'tool':'items.save','result':{'saved':True,'truncated':True}},
                        {'tool':'source.read','result':{'saved':True,'verified':True}}):
            with self.subTest(receipt=receipt),self.assertRaises(ValueError):
                assess(report(),[receipt],self.tools)
        for name,result in (('items.save',{'saved':True}),('items.reconcile',{'verified':True})):
            self.assertEqual(assess(report(),[{'tool':name,'result':result}],self.tools)['status'],'reported_complete')

    def test_queued_handoff_is_not_completed_work(self):
        receipts=[{'tool':'items.save','result':{'queued':True,'completed':False}}]
        with self.assertRaises(ValueError):assess(report(),receipts,self.tools)
        self.assertEqual(assess(report(kind='handoff'),receipts,self.tools)['status'],'reported_complete')

    def test_missing_and_invented_evidence_do_not_claim_completion(self):
        for encoded in (report(evidence=[]),report(evidence=['99']),report(status='partial')):
            with self.assertRaises(ValueError):assess(encoded,[],self.tools)
        self.assertEqual(assess('{}',[],self.tools)['status'],'unassessed')

    def test_shared_loop_revises_invalid_completion_without_repeating_write(self):
        backend=Mock()
        tool=dict(action='tool',tool='items.save',arguments_json='{}',reply='',document_title='',document='')
        finish=dict(action='finish',tool='',arguments_json=report(evidence=['99']),reply='Saved.',document_title='',document='')
        backend.call.side_effect=[tool,finish,dict(finish,arguments_json=report())]
        with tempfile.TemporaryDirectory() as tmp:
            result=research(backend,self.tools,{'message':'Save my item'},Path(tmp))
        self.assertEqual(result['status'],'reported_complete')
        self.write.assert_called_once()
        self.assertIn('Completion report is invalid',result['receipts'][1]['error'])

    def test_partial_report_delivers_unfinished_parts(self):
        backend=Mock();backend.call.return_value=dict(action='finish',tool='',
            arguments_json=report(status='needs_input',evidence=[],next_step='Which of the two matching items do you mean?'),
            reply='I found two matches.',document_title='',document='')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            result=research(backend,self.tools,{'message':'Save my item'},root)
            self.assertEqual(result['status'],'partial')
            self.assertIn('Which of the two matching items',result['reply'])
            self.assertEqual(research(backend,self.tools,{'message':'Save my item'},root),result)
        self.write.assert_not_called()
