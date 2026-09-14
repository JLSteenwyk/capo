import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from capo.calendar_actions import CalendarActions
from capo.calendar import event_body,GoogleCalendar,CalendarError
from capo.effects import UncertainEffect


def arguments():
    return dict(action='create',event_id='',title='Decide about an event',location='',start='2026-10-31',end='2026-11-01',all_day=True)


class CalendarActionTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.home=Path(tmp.name)
        self.adapter=CalendarActions(self.home,'owner','America/Los_Angeles',{})
        self.client=Mock();self.client.events.return_value=[]
        self.patch=patch('capo.calendar_actions.GoogleCalendar',return_value=self.client);self.patch.start();self.addCleanup(self.patch.stop)

    def test_existing_match_and_followup_never_create_another(self):
        body=event_body(arguments(),'America/Los_Angeles');body['id']='existing'
        self.client.events.return_value=[body]
        first=self.adapter.change(**arguments(),operation_id='one')
        second=CalendarActions(self.home,'owner','America/Los_Angeles',{}).change(**arguments(),operation_id='followup')
        self.assertTrue(first['already_exists']);self.assertEqual(first,second)
        self.client.request.assert_not_called()

    def test_lost_create_response_reconciles_on_followup_without_repeating(self):
        self.client.request.side_effect=TimeoutError()
        with self.assertRaises(TimeoutError):self.adapter.change(**arguments(),operation_id='one')
        event=self.client.request.call_args.kwargs['json']
        self.client.lookup.return_value=event
        restarted=CalendarActions(self.home,'owner','America/Los_Angeles',{})
        result=restarted.change(**arguments(),operation_id='followup')
        self.assertTrue(result['reconciled']);self.assertEqual(self.client.request.call_count,1)
        self.assertEqual(restarted.pending()['actions'],[])

    def test_missing_or_conflicting_evidence_does_not_repeat_write(self):
        self.client.request.side_effect=TimeoutError()
        with self.assertRaises(TimeoutError):self.adapter.change(**arguments(),operation_id='one')
        self.client.lookup.return_value=None
        with self.assertRaises(UncertainEffect):self.adapter.change(**arguments(),operation_id='followup')
        pending=self.adapter.pending()['actions'][0]
        self.assertFalse(self.adapter.reconcile(pending['id'])['confirmed'])
        self.assertEqual(self.client.request.call_count,1)

    def test_expired_credentials_or_failed_preflight_does_not_reserve_write(self):
        self.client.events.side_effect=CalendarError('Calendar unavailable')
        with self.assertRaises(CalendarError):self.adapter.change(**arguments(),operation_id='one')
        self.assertEqual(self.adapter.pending()['actions'],[])
        self.client.request.assert_not_called()

    def test_lookup_only_explicit_absence_confirms_deletion(self):
        client=GoogleCalendar.__new__(GoogleCalendar);client.session=Mock()
        response=client.session.get.return_value;response.status_code=403;response.ok=False
        with self.assertRaises(CalendarError):client.lookup('event')
        response.status_code=404;self.assertIsNone(client.lookup('event'))

    def test_two_action_request_recovers_only_unfinished_work(self):
        remote={};writes=[]
        self.client.events.side_effect=lambda start,end:list(remote.values())
        self.client.lookup.side_effect=lambda id:remote.get(id)
        def write(method,**kwargs):
            event=dict(kwargs['json']);remote[event['id']]=event;writes.append(event['id'])
            if len(writes)==2:raise TimeoutError()
            return event
        self.client.request.side_effect=write
        first=arguments();second=dict(first,title='Decide about another event',start='2026-11-05',end='2026-11-06')
        self.adapter.change(**first,operation_id='first')
        with self.assertRaises(TimeoutError):self.adapter.change(**second,operation_id='second')
        restarted=CalendarActions(self.home,'owner','America/Los_Angeles',{})
        self.assertTrue(restarted.change(**first,operation_id='followup-first')['already_exists'])
        self.assertTrue(restarted.change(**second,operation_id='followup-second')['reconciled'])
        self.assertEqual(len(writes),2);self.assertEqual(len(remote),2)

    def test_parallel_owner_changes_cannot_race_the_duplicate_check(self):
        import os,fcntl
        lock=self.adapter.effects.path.parent/'calendar-change.lock'
        fd=os.open(lock,os.O_CREAT|os.O_RDWR,0o600)
        try:
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaises(UncertainEffect):self.adapter.change(**arguments(),operation_id='overlap')
            self.client.events.assert_not_called();self.client.request.assert_not_called()
        finally:os.close(fd)
