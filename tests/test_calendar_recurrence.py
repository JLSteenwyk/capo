import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from datetime import datetime
from zoneinfo import ZoneInfo

from capo.calendar_actions import CalendarActions,equivalent
from capo.calendar import event_body,CalendarError
from capo.calendar_recurrence import rule
from capo.effects import UncertainEffect
from test_calendar_actions import arguments


class RecurrenceTests(unittest.TestCase):
    def test_common_rules_and_end_validation(self):
        for value in ('RRULE:FREQ=DAILY;COUNT=10','RRULE:FREQ=WEEKLY;BYDAY=MO,WE;INTERVAL=2',
                      'RRULE:FREQ=MONTHLY;BYDAY=-1FR','RRULE:FREQ=YEARLY;BYMONTH=6;BYMONTHDAY=1'):
            self.assertEqual(rule(rule(value)),rule(value))
        for value in ('RRULE:FREQ=SECONDLY','RRULE:FREQ=WEEKLY;BYDAY=1MO','RRULE:FREQ=DAILY;COUNT=0',
                      'RRULE:FREQ=DAILY;COUNT=2;UNTIL=20300101','RRULE:FREQ=DAILY\nATTENDEE:other',
                      'RRULE:FREQ=MONTHLY;BYMONTHDAY=32','RRULE:FREQ=DAILY;FREQ=WEEKLY'):
            with self.subTest(value=value),self.assertRaises(ValueError):rule(value)
        from capo.calendar_recurrence import equivalent_rules
        self.assertTrue(equivalent_rules(['RRULE:FREQ=WEEKLY;BYDAY=MO;WKST=MO'],['RRULE:FREQ=WEEKLY'],'2030-11-04'))
        with self.assertRaises(ValueError):rule('RRULE:FREQ=DAILY;UNTIL=20260101',True,'2026-10-31')
        with self.assertRaises(ValueError):rule('RRULE:FREQ=DAILY;UNTIL=20270101',False,'2026-10-31T09:00:00-07:00')

    def test_timed_series_keeps_named_zone_across_dst(self):
        plan=dict(arguments(),all_day=False,start='2026-10-31T09:00:00-07:00',end='2026-10-31T10:00:00-07:00',recurrence='RRULE:FREQ=WEEKLY;COUNT=3')
        body=event_body(plan,'America/Los_Angeles')
        self.assertEqual(body['start']['timeZone'],'America/Los_Angeles')
        self.assertEqual(body['recurrence'],['RRULE:COUNT=3;FREQ=WEEKLY'])
        # The stored named zone, rather than a fixed offset, determines later local times.
        later=datetime(2026,11,7,9,tzinfo=ZoneInfo(body['start']['timeZone']))
        self.assertEqual(later.utcoffset().total_seconds(),-8*3600)

    def test_create_reconcile_and_duplicate_check_expanded_instance(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter=CalendarActions(Path(tmp),'owner','America/Los_Angeles',{})
            client=Mock();client.events.return_value=[];remote={};writes=[]
            def send(method,**kwargs):
                self.assertEqual(method,'POST');body=copy.deepcopy(kwargs['json']);remote[body['id']]=body;writes.append(body)
                raise CalendarError('Lost response')
            client.request.side_effect=send;client.lookup.side_effect=lambda id:copy.deepcopy(remote.get(id))
            args=dict(arguments(),recurrence='RRULE:FREQ=WEEKLY;COUNT=4')
            with patch('capo.calendar_actions.GoogleCalendar',return_value=client):
                with self.assertRaises(CalendarError):adapter.change(**args,operation_id='first')
                pending=adapter.pending()['actions'][0]
                parent=next(iter(remote));expected=remote[parent]['recurrence']
                remote[parent]['recurrence']=['RRULE:FREQ=DAILY;COUNT=4']
                self.assertFalse(adapter.reconcile(pending['id'])['confirmed'])
                self.assertEqual(len(writes),1)
                remote[parent]['recurrence']=expected
                self.assertTrue(adapter.reconcile(pending['id'])['verified'])
                parent=next(iter(remote));instance=dict(remote[parent],id='instance',recurringEventId=parent);instance.pop('recurrence')
                client.events.return_value=[instance]
                result=adapter.change(**args,operation_id='second')
                self.assertTrue(result['already_exists']);self.assertEqual(result['event_id'],parent)
                self.assertEqual(len(writes),1)
                altered=copy.deepcopy(remote[parent]);altered['recurrence']=['RRULE:FREQ=DAILY;COUNT=4']
                self.assertFalse(equivalent(altered,writes[0]))
                self.assertFalse(equivalent(instance,event_body(arguments(),'America/Los_Angeles')))

    def test_shared_tool_composes_inspection_and_creation(self):
        from capo.capabilities import shared_tools,Documents
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp);client=Mock();client.events.return_value=[];saved={}
            def send(method,**kwargs):
                saved.update(copy.deepcopy(kwargs['json']));return saved
            client.request.side_effect=send;client.lookup.side_effect=lambda id:copy.deepcopy(saved) if saved.get('id')==id else None
            with patch('capo.calendar.GoogleCalendar',return_value=client),patch('capo.calendar_actions.GoogleCalendar',return_value=client):
                tools=shared_tools(home,{'calendar':{'enabled':True,'timezone':'America/Los_Angeles'}},Documents(home,'owner'))
                tools.call('calendar.events',{'start':'2026-10-31T00:00:00-07:00','end':'2026-11-01T00:00:00-07:00'})
                value=tools.call('calendar.change',dict(arguments(),recurrence='FREQ=MONTHLY;COUNT=2',calendar_id=''),operation_id='create')
                self.assertTrue(value['verified']);self.assertEqual(saved['recurrence'],['RRULE:COUNT=2;FREQ=MONTHLY'])
                with self.assertRaises(ValueError):tools.call('calendar.change',dict(arguments(),action='update',recurrence='RRULE:FREQ=DAILY'),operation_id='edit')
