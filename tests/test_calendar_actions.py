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

    def test_update_switches_date_representation_without_losing_other_fields(self):
        from capo.calendar import apply
        import copy
        for old_all_day in (True, False):
            with self.subTest(old_all_day=old_all_day):
                old = event_body(dict(arguments(), all_day=old_all_day,
                    start='2026-10-31' if old_all_day else '2026-10-31T18:00:00-07:00',
                    end='2026-11-01' if old_all_day else '2026-10-31T20:00:00-07:00'), 'America/Los_Angeles')
                old.update(id='event', etag='version1', organizer={'self':True}, description='Keep these notes')
                saved = copy.deepcopy(old)
                def request(method, event_id, **kwargs):
                    if method == 'GET': return copy.deepcopy(saved)
                    self.assertEqual(method, 'PATCH')
                    self.assertEqual(kwargs['headers']['If-Match'], 'version1')
                    for key, value in kwargs['json'].items():
                        if key in ('start', 'end'):
                            for field, item in value.items():
                                if item is None: saved[key].pop(field, None)
                                else: saved[key][field] = item
                            self.assertNotEqual('date' in saved[key], 'dateTime' in saved[key])
                        else: saved[key] = value
                    return copy.deepcopy(saved)
                client = Mock();client.request.side_effect = request
                plan = dict(arguments(), action='update', event_id='event', all_day=not old_all_day,
                    start='2026-10-31T18:00:00-07:00' if old_all_day else '2026-10-31',
                    end='2026-10-31T20:00:00-07:00' if old_all_day else '2026-11-01')
                directory = self.home / str(old_all_day);directory.mkdir()
                apply(client, plan, [old], 'America/Los_Angeles', directory)
                self.assertEqual(saved['description'], 'Keep these notes')
                self.assertEqual('date' in saved['start'], not old_all_day)
                if not old_all_day: self.assertNotIn('timeZone', saved['start'])

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

    def test_acknowledged_create_requires_authoritative_saved_event(self):
        self.client.request.return_value={'accepted':True}
        self.client.lookup.return_value=None
        with self.assertRaises(UncertainEffect):
            self.adapter.change(**arguments(),operation_id='create')
        with self.assertRaises(UncertainEffect):
            self.adapter.change(**arguments(),operation_id='retry')
        self.assertEqual(self.client.request.call_count,1)
        actual=dict(self.client.request.call_args.kwargs['json'])
        self.client.lookup.return_value=actual
        pending=self.adapter.pending()['actions'][0]
        actual['start']['dateTime']='2026-10-31T09:00:00-07:00'
        self.assertFalse(self.adapter.reconcile(pending['id'])['confirmed'])
        del actual['start']['dateTime']
        result=self.adapter.reconcile(pending['id'])
        self.assertTrue(result['verified'])
        self.assertEqual(self.client.request.call_count,1)

    def test_update_verifies_preserved_details_and_reconciles_after_restart(self):
        import copy
        old=dict(event_body(arguments(),'America/Los_Angeles'),id='event',etag='v1',
                 organizer={'self':True},description='Keep these notes',reminders={'useDefault':True})
        self.adapter.cache['event']=copy.deepcopy(old)
        remote=copy.deepcopy(old)
        def request(method,id,**kwargs):
            if method=='GET':return copy.deepcopy(old)
            self.assertEqual(method,'PATCH')
            for key,value in kwargs['json'].items():
                remote[key]={k:v for k,v in value.items() if v is not None} if isinstance(value,dict) else value
            remote.pop('description')
            return remote
        self.client.request.side_effect=request
        self.client.lookup.side_effect=lambda id:copy.deepcopy(remote)
        args=dict(arguments(),action='update',event_id='event',title='Updated decision')
        with self.assertRaises(UncertainEffect):
            self.adapter.change(**args,operation_id='update')
        restarted=CalendarActions(self.home,'owner','America/Los_Angeles',{})
        pending=restarted.pending()['actions'][0]
        self.assertFalse(restarted.reconcile(pending['id'])['confirmed'])
        remote['description']='Keep these notes'
        self.assertTrue(restarted.reconcile(pending['id'])['verified'])
        self.assertEqual(len([c for c in self.client.request.call_args_list if c.args[0]=='PATCH']),1)

    def test_delete_requires_confirmed_absence(self):
        old=dict(event_body(arguments(),'America/Los_Angeles'),id='event',etag='v1',organizer={'self':True})
        self.adapter.cache['event']=old
        self.client.request.return_value=old
        self.client.lookup.return_value=old
        args=dict(arguments(),action='delete',event_id='event')
        with self.assertRaises(UncertainEffect):
            self.adapter.change(**args,operation_id='delete')
        pending=self.adapter.pending()['actions'][0]
        self.client.lookup.return_value=None
        self.assertTrue(self.adapter.reconcile(pending['id'])['verified'])
        self.assertEqual(len([c for c in self.client.request.call_args_list if c.args[0]=='DELETE']),1)

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
