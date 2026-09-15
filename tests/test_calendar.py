import hashlib
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from capo.calendar import (CalendarConversation, CalendarError, GoogleCalendar,
                           apply, event_body, writable, schedule)
from capo.conversation import ConversationError, ConversationPending


class CalendarTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.plan = dict(action='create', title='Walk', location='', event_id='',
                         start='2026-09-14T09:00:00-07:00', end='2026-09-14T09:30:00-07:00',
                         all_day=False, reply='')
        self.event = dict(id='event123', summary='Walk', etag='"v1"', organizer={'self': True})
        self.client = Mock()
        self.client.request.return_value = self.event.copy()

    def test_create_private_no_guests_and_no_duplicate_replay(self):
        self.assertIn('Added', apply(self.client, self.plan, [], 'America/Los_Angeles', self.home))
        args = self.client.request.call_args
        self.assertEqual(args.args, ('POST',))
        self.assertEqual(args.kwargs['params'], {'sendUpdates': 'none'})
        body = args.kwargs['json']
        self.assertRegex(body['id'], '^[0-9a-f]{64}$')
        self.assertNotIn('attendees', body)
        self.assertEqual((self.home / 'effect.json').stat().st_mode & 0o777, 0o600)
        with self.assertRaises(CalendarError):
            apply(self.client, self.plan, [], 'America/Los_Angeles', self.home)
        self.assertEqual(self.client.request.call_count, 1)

    def test_update_uses_etag_and_patch(self):
        self.plan.update(action='update', event_id='event123')
        apply(self.client, self.plan, [self.event], 'America/Los_Angeles', self.home)
        args = self.client.request.call_args
        self.assertEqual(args.args, ('PATCH', 'event123'))
        self.assertEqual(args.kwargs['headers'], {'If-Match': '"v1"'})
        self.assertNotIn('description', args.kwargs['json'])

    def test_delete_uses_exact_target_and_etag(self):
        self.plan.update(action='delete', event_id='event123')
        apply(self.client, self.plan, [self.event], 'America/Los_Angeles', self.home)
        args = self.client.request.call_args
        self.assertEqual(args.args, ('DELETE', 'event123'))
        self.assertNotIn('json', args.kwargs)
        self.assertEqual(args.kwargs['headers'], {'If-Match': '"v1"'})

    def test_changed_event_is_not_written(self):
        self.plan.update(action='delete', event_id='event123')
        self.client.request.return_value = dict(self.event, etag='"v2"')
        with self.assertRaisesRegex(CalendarError, 'changed'):
            apply(self.client, self.plan, [self.event], 'America/Los_Angeles', self.home)
        self.assertEqual(self.client.request.call_count, 1)
        self.assertFalse((self.home / 'effect.json').exists())

    def test_unknown_event_not_written(self):
        self.plan.update(action='delete', event_id='unknown')
        with self.assertRaises(CalendarError):
            apply(self.client, self.plan, [self.event], 'America/Los_Angeles', self.home)
        self.client.request.assert_not_called()

    def test_recurring_guest_and_unowned_events_not_writable(self):
        for changes in ({'attendees': [{'email': 'guest@example.invalid'}]},
                        {'recurrence': ['RRULE:FREQ=WEEKLY']}, {'recurringEventId': 'parent'},
                        {'organizer': {'self': False}}, {'eventType': 'outOfOffice'}, {'etag': ''}):
            event = dict(self.event, **changes)
            self.assertFalse(writable(event))
            self.plan.update(action='delete', event_id=event['id'])
            with self.assertRaises(CalendarError):
                apply(self.client, self.plan, [event], 'America/Los_Angeles', self.home)
        self.client.request.assert_not_called()

    def test_uncertain_network_write_is_not_retried(self):
        self.client.request.side_effect = TimeoutError('private data')
        with self.assertRaises(TimeoutError):
            apply(self.client, self.plan, [], 'America/Los_Angeles', self.home)
        with self.assertRaises(CalendarError):
            apply(self.client, self.plan, [], 'America/Los_Angeles', self.home)
        self.client.request.assert_called_once()

    def test_dates_offsets_and_all_day(self):
        for changes in ({'end': self.plan['start']}, {'start': '2026-09-14T09:00:00'},
                        {'start': '2026-09-14T09:00:00-08:00'},
                        {'start': '2026-03-08T02:15:00-08:00', 'end': '2026-03-08T03:30:00-07:00'}):
            with self.assertRaises(ValueError):
                event_body(dict(self.plan, **changes), 'America/Los_Angeles')
        body = event_body(dict(self.plan, all_day=True, start='2026-09-14', end='2026-09-15'), 'America/Los_Angeles')
        self.assertEqual(body['end'], {'date': '2026-09-15'})

    def test_api_412_and_http_failure_redacted(self):
        client = GoogleCalendar.__new__(GoogleCalendar)
        client.session = Mock()
        for code in (412, 403):
            client.session.request.return_value = Mock(status_code=code, ok=False, text='SECRET')
            with self.assertRaises(CalendarError) as error:
                client.request('DELETE', 'a/b', headers={'If-Match': '"v1"'})
            self.assertNotIn('SECRET', str(error.exception))
            self.assertTrue(client.session.request.call_args.args[1].endswith('/a%2Fb'))

    def test_truncated_list_requires_narrower_window(self):
        client = GoogleCalendar.__new__(GoogleCalendar)
        client.request = Mock(return_value={'items': [], 'nextPageToken': 'next'})
        with self.assertRaises(CalendarError):
            client.events(self.plan['start'], self.plan['end'])

    def finish(self, router):
        context = dict(aliases=[], objectives=[], timezone='America/Los_Angeles', message='What is on my calendar?')
        for _ in range(500):
            try:
                return router.poll('test', context)
            except ConversationPending:
                time.sleep(.005)
        self.fail('Calendar worker did not finish')

    def test_read_does_not_write_and_replay_uses_receipt(self):
        router = CalendarConversation(self.home)
        window = dict(action='window', start=self.plan['start'], end=self.plan['end'], question='Here is your schedule.')
        with patch('capo.calendar.GoogleCalendar', return_value=self.client), patch('capo.calendar.Providers') as provider:
            self.client.events.return_value = []
            provider.return_value.call.side_effect = [window, dict(self.plan, action='read', reply='Your calendar is clear.')]
            self.assertEqual(self.finish(router)['reply'], 'No events on your primary calendar in that time range.')
            self.assertEqual(self.finish(router)['reply'], 'No events on your primary calendar in that time range.')
            self.assertEqual(provider.return_value.call.call_count, 2)
            self.client.request.assert_not_called()

    def test_clarification_and_oauth_failure_redacted(self):
        with patch('capo.calendar.GoogleCalendar', return_value=self.client), patch('capo.calendar.Providers') as provider:
            provider.return_value.call.return_value = dict(action='ask', start='', end='', question='What time?')
            self.assertEqual(self.finish(CalendarConversation(self.home))['reply'], 'What time?')
            self.client.events.assert_not_called()
        with patch('capo.calendar.GoogleCalendar', side_effect=RuntimeError('SECRET')):
            result = self.finish(CalendarConversation(self.home / 'other'))
            self.assertNotIn('SECRET', result['reply'])

    def test_schedule_contains_events_even_when_model_only_gives_intro(self):
        event = dict(self.event, start={'dateTime': self.plan['start']}, end={'dateTime': self.plan['end']})
        with patch('capo.calendar.GoogleCalendar', return_value=self.client), patch('capo.calendar.Providers') as provider:
            self.client.events.return_value = [event]
            provider.return_value.call.side_effect = [
                dict(action='window', start=self.plan['start'], end=self.plan['end'], question='Here is your schedule.'),
                dict(self.plan, action='read', reply='Here is your schedule.')]
            result = self.finish(CalendarConversation(self.home))['reply']
            self.assertIn('Walk', result)
            self.assertIn('9:00 AM', result)
            self.assertIn('9:30 AM', result)
            self.client.request.assert_not_called()

    def test_schedule_all_day_and_overflow_are_explicit(self):
        event = dict(summary='Holiday', start={'date': '2026-09-14'}, end={'date': '2026-09-15'})
        self.assertIn('all day: Holiday', schedule([event], 'America/Los_Angeles'))
        result = schedule([event] * 100, 'America/Los_Angeles')
        self.assertLess(len(result), 2000)
        self.assertIn('more events', result)

    def test_restart_never_retries_uncertain_operation(self):
        router = CalendarConversation(self.home)
        directory = router.root / hashlib.sha256(b'test').hexdigest()
        directory.mkdir()
        (directory / 'started.json').write_text('{}')
        with patch('capo.calendar.GoogleCalendar') as client:
            with self.assertRaisesRegex(ConversationError, 'check Google Calendar'):
                self.finish(router)
            self.assertIn('interrupted', self.finish(router)['reply'])
            client.assert_not_called()

    def test_owner_calendar_route_uses_shared_action_receipts(self):
        from capo.request_memory import RequestMemory
        router=CalendarConversation(self.home,'owner')
        context=dict(aliases=[],objectives=[],timezone='America/Los_Angeles',
                     message='Create the walk event.',request_thread='thread',request_event='test')
        window=dict(action='window',start=self.plan['start'],end=self.plan['end'],question='')
        with patch('capo.calendar.GoogleCalendar',return_value=self.client),patch('capo.calendar_actions.GoogleCalendar',return_value=self.client),patch('capo.calendar.Providers') as provider:
            self.client.events.return_value=[]
            remote={}
            def save(method,**kwargs):
                value=dict(kwargs['json']);remote[value['id']]=value;return value
            self.client.request.side_effect=save
            self.client.lookup.side_effect=lambda id:remote.get(id)
            provider.return_value.call.side_effect=[window,self.plan]
            for _ in range(500):
                try:
                    result=router.poll('test',context);break
                except ConversationPending:time.sleep(.005)
            else:self.fail('Calendar worker did not finish')
            self.assertIn('Added',result['reply'])
            actions=RequestMemory(self.home,'owner','thread').actions('')['actions']
            self.assertEqual(len(actions),1);self.assertTrue(actions[0]['result']['changed'])
            self.assertEqual(self.client.request.call_count,1)
