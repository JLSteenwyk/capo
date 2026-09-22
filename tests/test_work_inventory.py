import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch

from capo.contracts import TEXT,object_schema
from capo.research_tools import ReadTool,ReadTools,research
from capo.request_outcomes import assess,OutcomeError
from capo.work_inventory import WorkInventory


def step(tool='',arguments=None,reply='Done.'):
    return dict(action='tool' if tool else 'finish',tool=tool,arguments_json=json.dumps(arguments or {}),
                reply='' if tool else reply,document_title='',document='')


def outcome(id,evidence,status='complete'):
    return dict(item_id=id,requirement='Requested '+id,kind='action',status=status,evidence=evidence,
                next_step='' if status=='complete' else 'Finish this item after verification.')


class WorkInventoryTests(unittest.TestCase):
    def test_batch_cannot_finish_after_only_one_item_or_reuse_its_receipt(self):
        provider=Mock();writes=[]
        tools=ReadTools([ReadTool('items.save','Save',object_schema({'name':TEXT}),
            lambda name,operation_id:(writes.append(name) or {'saved':True,'id':name}),mutates=True)])
        plan=[dict(id=x,requirement='Requested '+x,kind='action') for x in ('breakfast','talk','lunch')]
        provider.call.side_effect=[step('work.track',dict(items=plan,source_receipts=[])),
            step('items.save',{'name':'breakfast'}),
            step(arguments={'outcomes':[outcome('breakfast',['1'])]}),
            step(arguments={'outcomes':[outcome(x,['1']) for x in ('breakfast','talk','lunch')]}),
            step('items.save',{'name':'talk'}),step('items.save',{'name':'lunch'}),
            step(arguments={'outcomes':[outcome('breakfast',['1']),outcome('talk',['4']),outcome('lunch',['5'])]})]
        with tempfile.TemporaryDirectory() as tmp:
            result=research(provider,tools,{'message':'Create all three requested items'},Path(tmp),max_calls=10)
        self.assertEqual(writes,['breakfast','talk','lunch'])
        self.assertEqual(result['status'],'reported_complete')
        self.assertIn('Missing tracked items',result['receipts'][2]['error'])
        self.assertIn('cannot reuse',result['receipts'][3]['error'])

    def test_inventory_survives_restart_and_rejects_dropped_or_retyped_items(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp);item=dict(id='one',requirement='Save one',kind='action')
            WorkInventory(path,[]).track([item],[])
            restarted=WorkInventory(path,[])
            for items in ([dict(item,kind='answer')],[dict(item,id='two')]):
                with self.assertRaises(ValueError):restarted.track(items,[])
            with self.assertRaises(ValueError):restarted.track([item],['99'])
            self.assertEqual(restarted.read()['items'],[item])
            with self.assertRaises(OutcomeError):assess('{}',[],ReadTools([]),inventory=[item])
            partial=assess(json.dumps({'outcomes':[outcome('one',[],'partial')]}),[],ReadTools([]),inventory=[item])
            self.assertEqual(partial['status'],'partial')

    def test_queued_flag_alone_does_not_prove_completion(self):
        tool=ReadTool('items.save','Save',object_schema({}),Mock(),mutates=True)
        with self.assertRaises(OutcomeError):
            assess(json.dumps({'outcomes':[outcome('one',['0'])]}),
                [{'tool':tool.name,'result':{'queued':True}}],ReadTools([tool]))
