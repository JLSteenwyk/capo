import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from capo.calendar_patch import CalendarPatch
from capo.effects import UncertainEffect
from capo.research_tools import ToolInputError


class PatchTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.home=Path(tmp.name)
        self.event={'id':'event','etag':'v1','summary':'Lunch','description':'Existing notes','location':'Old address',
                    'organizer':{'self':True},'start':{'date':'2030-01-02'},'end':{'date':'2030-01-03'},
                    'attendees':[{'email':'guest@example.invalid','responseStatus':'accepted'}]}
        self.client=Mock();self.client.lookup.side_effect=lambda _:copy.deepcopy(self.event)
        def apply(*args,**kwargs):self.event.update(kwargs['json']);return copy.deepcopy(self.event)
        self.client.request.side_effect=apply
        patcher=patch('capo.calendar_patch.GoogleCalendar',return_value=self.client);patcher.start();self.addCleanup(patcher.stop)
        self.adapter=CalendarPatch(self.home,'owner','primary',{'event':copy.deepcopy(self.event)})

    def change(self,fields,op='one',scope='event'):
        return self.adapter.patch('event',fields,scope,False,op)

    def test_notes_and_address_patch_preserve_guests_times_and_unrelated_fields(self):
        result=self.change({'description':'Existing notes\nResearch: https://example.invalid/info','location':'New address'})
        self.assertTrue(result['verified']);kwargs=self.client.request.call_args.kwargs
        self.assertEqual(set(kwargs['json']),{'description','location'})
        self.assertEqual(kwargs['headers'],{'If-Match':'v1'});self.assertEqual(kwargs['params'],{'sendUpdates':'none'})
        self.assertEqual(self.event['summary'],'Lunch');self.assertEqual(self.event['attendees'][0]['responseStatus'],'accepted')

    def test_timeout_after_acceptance_reconciles_without_second_write(self):
        original=self.client.request.side_effect
        def lost(*args,**kwargs):original(*args,**kwargs);raise TimeoutError()
        self.client.request.side_effect=lost
        with self.assertRaises(TimeoutError):self.change({'description':'Updated'})
        self.assertTrue(self.change({'description':'Updated'},'retry')['verified'])
        self.assertEqual(self.client.request.call_count,1)

    def test_unconfirmed_patch_does_not_repeat(self):
        self.client.request.side_effect=TimeoutError()
        with self.assertRaises(TimeoutError):self.change({'description':'Updated'})
        with self.assertRaises(UncertainEffect):self.change({'description':'Updated'},'retry')
        self.assertEqual(self.client.request.call_count,1)

    def test_stale_etag_foreign_organizer_and_unsupported_fields_rejected(self):
        for fields in ({},{'attendees':[]},{'summary':''}):
            with self.assertRaises(ToolInputError):self.change(fields)
        self.event['etag']='v2'
        with self.assertRaises(ToolInputError):self.change({'location':'New'})
        self.event['etag']='v1';self.event['organizer']={'self':False}
        with self.assertRaises(ToolInputError):self.change({'location':'New'})
        self.client.request.assert_not_called()

    def test_series_scope_must_be_explicit_and_preserves_rule(self):
        self.event['recurrence']=['RRULE:FREQ=WEEKLY']
        with self.assertRaises(ToolInputError):self.change({'description':'Series notes'})
        self.assertTrue(self.change({'description':'Series notes'},scope='series')['verified'])
        self.assertEqual(self.event['recurrence'],['RRULE:FREQ=WEEKLY'])

    def test_readback_detects_unrelated_field_loss(self):
        def broken(*args,**kwargs):self.event.update(kwargs['json']);self.event['attendees']=[]
        self.client.request.side_effect=broken
        with self.assertRaises(UncertainEffect):self.change({'description':'Updated'})

    def test_uncertain_patch_is_discoverable_in_shared_pending_tools(self):
        from capo.calendar_actions import CalendarActions
        self.client.request.side_effect=TimeoutError()
        with self.assertRaises(TimeoutError):self.change({'location':'New address'})
        actions=CalendarActions(self.home,'owner','America/Los_Angeles',{})
        pending=actions.pending()['actions']
        self.assertEqual(pending[0]['action'],'patch')
        self.assertFalse(actions.reconcile(pending[0]['id'])['confirmed'])
        self.event['location']='New address'
        self.assertTrue(actions.reconcile(pending[0]['id'])['verified'])
        self.assertEqual(self.client.request.call_count,1)

    def test_shared_registry_patch_composes_with_inspection(self):
        from capo.calendar_tools import CalendarTools
        tools=CalendarTools('America/Los_Angeles',lambda *_:self.client)
        tools.event('primary','event')
        patch_tool=next(t for t in tools.action_tools(self.home,'owner') if t.name=='calendar.patch')
        result=patch_tool.execute(calendar_id='primary',event_id='event',fields={'description':'Existing notes and verified details'},scope='event',notify_guests=True,operation_id='composed')
        self.assertTrue(result['verified'])
        self.assertEqual(self.client.request.call_args.kwargs['params'],{'sendUpdates':'all'})
