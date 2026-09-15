import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from capo.calendar_tools import CalendarTools
from capo.calendar_actions import CalendarActions
from capo.calendar import GoogleCalendar
from capo.contracts import validate, object_schema, TEXT
from capo.research_tools import ReadTools


START='2030-01-02T00:00:00-08:00'
END='2030-01-03T00:00:00-08:00'


def event(title):
    return {'id':'same-id','summary':title,'description':'Bring the proposal.','etag':'version1',
            'organizer':{'self':True},'start':{'date':'2030-01-02'},'end':{'date':'2030-01-03'}}


class CalendarToolTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.home=Path(self.temp.name)
        self.primary=Mock();self.work=Mock();self.clients={'primary':self.primary,'work':self.work,'shared':Mock()}
        self.factory=lambda calendar_id='primary':self.clients[calendar_id]
        self.tools=CalendarTools('America/Los_Angeles', self.factory)
        self.primary.calendar_list.return_value={'items':[
            {'id':'owner@example.invalid','primary':True,'accessRole':'owner','summary':'Personal'},
            {'id':'work','accessRole':'owner','summary':'Work'},
            {'id':'shared','accessRole':'reader','summary':'Shared'}]}
        self.tools.list('')

    def test_discovery_inspection_and_query_identity(self):
        self.primary.events_page.return_value={'items':[event('Personal')],'nextPageToken':'next'}
        self.work.events_page.return_value={'items':[event('Work')]}
        one=self.tools.search('owner@example.invalid',START,END,'','')
        two=self.tools.search('work',START,END,'','')
        self.assertEqual(one['calendar_id'],'primary')
        self.assertEqual(two['calendar_id'],'work')
        self.assertEqual(len(self.tools.cache),2)
        with self.assertRaises(ValueError):self.tools.search('work',START,END,'','next')
        with self.assertRaises(ValueError):self.tools.search('primary',START,END,'other','next')
        with self.assertRaises(ValueError):self.tools.search('unknown',START,END,'','')
        self.primary.lookup.return_value=dict(event('Personal'),attendees=[{'email':'guest@example.invalid'}])
        details=self.tools.event('primary','same-id')['event']
        self.assertEqual(details['description'],'Bring the proposal.')
        self.assertFalse(details['editable_by_capo'])
        self.assertEqual(details['attendees'][0]['email'],'guest@example.invalid')
        self.assertEqual(self.tools.cache[('work','same-id')]['summary'],'Work')

    def test_missing_event_removes_cached_write_target(self):
        self.primary.events.return_value=[event('Personal')]
        self.tools.events(START,END)
        self.primary.lookup.return_value=None
        self.assertFalse(self.tools.event('primary','same-id')['found'])
        self.assertNotIn('same-id',self.tools.primary_cache)

    def test_optional_calendar_keeps_legacy_schema_and_does_not_loosen_other_fields(self):
        schema=object_schema({'required':TEXT});schema['properties']['optional']=TEXT
        validate({'required':'yes'},schema)
        validate({'required':'yes','optional':'yes'},schema)
        with self.assertRaises(ValueError):validate({'optional':'yes'},schema)
        with self.assertRaises(ValueError):validate({'required':'yes','extra':'no'},schema)

    def test_secondary_write_and_lost_response_reconcile_in_same_calendar(self):
        original=event('Work')
        self.work.events_page.return_value={'items':[original]}
        self.tools.search('work',START,END,'','')
        registry=ReadTools(self.tools.action_tools(self.home,'owner'))
        args={'action':'update','calendar_id':'work','event_id':'same-id','title':'Work updated','location':'',
              'start':'2030-01-02','end':'2030-01-03','all_day':True}
        def request(method,event_id,**kwargs):
            if method=='GET':return copy.deepcopy(original)
            self.assertEqual(method,'PATCH')
            self.work.lookup.return_value=dict(original,summary='Work updated')
            raise TimeoutError()
        self.work.request.side_effect=request
        with patch('capo.calendar_actions.GoogleCalendar',side_effect=self.factory):
            with self.assertRaises(TimeoutError):registry.call('calendar.change',args,operation_id='change-work')
            restored=CalendarActions(self.home,'owner','America/Los_Angeles',{})
            pending=restored.pending()['actions']
            self.assertEqual(pending[0]['calendar_id'],'work')
            self.assertTrue(restored.reconcile(pending[0]['id'])['reconciled'])
        self.primary.request.assert_not_called()
        self.primary.lookup.assert_not_called()
        self.assertEqual(self.work.request.call_count,2)

    def test_read_access_does_not_authorize_shared_calendar_writes(self):
        registry=ReadTools(self.tools.action_tools(self.home,'owner'))
        args={'action':'create','calendar_id':'shared','event_id':'','title':'No','location':'',
              'start':'2030-01-02','end':'2030-01-03','all_day':True}
        with self.assertRaises(PermissionError):registry.call('calendar.change',args,operation_id='forbidden')
        self.clients['shared'].request.assert_not_called()

    def test_missing_discovery_grant_is_not_a_blanket_event_access_failure(self):
        from types import SimpleNamespace
        from capo.calendar import CalendarDiscoveryRequired
        client=GoogleCalendar.__new__(GoogleCalendar);client.session=Mock()
        client.session.get.return_value=SimpleNamespace(status_code=403)
        with self.assertRaises(CalendarDiscoveryRequired):client.calendar_list()
        client.session.request.return_value=SimpleNamespace(status_code=200,ok=True,content=b'{}',json=lambda:{'items':[]})
        self.assertEqual(client.events(START,END),[])

    def test_calendar_identifier_is_encoded_as_one_path_component(self):
        client=GoogleCalendar.__new__(GoogleCalendar);client.calendar_id='a/b@example.invalid'
        self.assertTrue(client.events_url.endswith('/a%2Fb%40example.invalid/events'))
        client=GoogleCalendar.__new__(GoogleCalendar)
        self.assertTrue(client.events_url.endswith('/primary/events'))
