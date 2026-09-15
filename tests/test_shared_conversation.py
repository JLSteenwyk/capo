import base64
import hashlib
import json
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from capo.capabilities import owner_key
from capo.conversation import ConversationPending, _write
from capo.slack import SlackService, legacy_conversation
from capo.specialist_tools import SpecialistTools
from capo.store import Store


def step(tool, **arguments):
    return dict(action='tool', tool=tool, arguments_json=json.dumps(arguments), reply='', document_title='', document='')


def finish(text):
    return dict(action='finish', tool='', arguments_json='{}', reply=text, document_title='', document='')


class SharedConversationTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.config = {'team_id': 'T123', 'channel_id': 'C123', 'owner_user_id': 'U123', 'auto_run': False,
            'repositories': {'project': {'path': str(self.home), 'checks': ['python3 -c "print(1)"']}},
            'calendar': {'enabled': True, 'timezone': 'America/Los_Angeles'}, 'gmail': {'enabled': True}}
        self.store = Store(self.home)
        self.addCleanup(self.store.db.close)
        self.service = SlackService(self.store, self.config, Mock())

    def body(self, text, event='event', thread=None):
        value = {'team_id': 'T123', 'event_id': event, 'event': {'type': 'app_mention', 'channel': 'C123',
                 'user': 'U123', 'ts': '123.456' if thread is None else '124.456', 'text': '<@UBOT> '+text}}
        if thread:
            value['event']['thread_ts'] = thread
        self.store.enqueue_slack(event, value)
        return value

    def run_request(self, body, service=None):
        service = service or self.service
        for _ in range(600):
            try:
                return service.dispatch(body['event_id'], body)
            except ConversationPending:
                time.sleep(.005)
        self.fail('Shared request did not finish')

    def test_one_loop_combines_calendar_inspection_and_email(self):
        body = self.body('Prepare for my meeting using its details and the related email.')
        event = {'id': 'meeting', 'summary': 'Planning', 'description': 'Review the inventory',
                 'start': {'dateTime': '2026-11-02T09:00:00-08:00'}, 'end': {'dateTime': '2026-11-02T10:00:00-08:00'}}
        with patch('capo.capabilities.Providers') as providers, patch('capo.calendar.GoogleCalendar') as calendar, \
                patch('capo.gmail.Gmail') as gmail, patch('capo.conversation.Providers', side_effect=AssertionError('No classifier')):
            calendar.return_value.events.return_value = [event]
            calendar.return_value.lookup.return_value = event
            gmail.return_value.get.side_effect = [{'messages': [{'id': 'mail'}]},
                {'payload': {'mimeType': 'text/plain', 'body': {'data': base64.urlsafe_b64encode(b'Bring the inventory totals.').decode()}}}]
            providers.return_value.call.side_effect = [
                step('calendar.events', start='2026-11-02T00:00:00-08:00', end='2026-11-03T00:00:00-08:00'),
                step('calendar.event', calendar_id='primary', event_id='meeting'),
                step('mail.search', query='subject:Planning', page_size='1', page_token=''),
                step('mail.read', ids=['mail'], strip_quotes=False), finish('Bring the inventory totals to Planning at 9 a.m.')]
            response = self.run_request(body)
            self.assertIn('inventory totals', response)
            calendar.return_value.lookup.assert_called_once_with('meeting')
            self.assertEqual(gmail.return_value.get.call_count, 2)
            final_prompt = providers.return_value.call.call_args.args[1]
            self.assertIn('Review the inventory', final_prompt)
            self.assertIn('Bring the inventory totals.', final_prompt)
            self.assertEqual(len(providers.return_value.call.call_args_list), 5)
            self.assertEqual(self.run_request(body, SlackService(self.store, self.config, Mock())), response)
            self.assertEqual(providers.return_value.call.call_count, 5)
        self.assertFalse(legacy_conversation(self.home, 'event'))
        self.assertEqual(self.store.list(), [])

    def test_followup_preserves_original_request_without_mentions(self):
        original = self.body('Research these dates and create reminders.')
        with patch('capo.capabilities.Providers') as providers:
            providers.return_value.call.return_value = finish('Which year should I use?')
            self.run_request(original)
            followup = self.body('Use 2026.', 'followup', '123.456')
            followup['event'].update(type='message', text='Use 2026.')
            providers.return_value.call.return_value = finish('I will use 2026.')
            self.run_request(followup)
            data = json.loads(providers.return_value.call.call_args.args[1].splitlines()[-1])
            self.assertIn('create reminders', data['request']['request_state']['original_request']['message'])
            self.assertEqual(data['request']['message'], 'Use 2026.')

    def test_cancellation_stops_selected_tool_and_help_stays_available(self):
        started, release = threading.Event(), threading.Event()
        def provider(*args):
            started.set()
            release.wait(3)
            return step('calendar.events', start='2026-11-02T00:00:00-08:00', end='2026-11-03T00:00:00-08:00')
        body = self.body('Check my calendar.')
        with patch('capo.capabilities.Providers') as providers, patch('capo.calendar.GoogleCalendar') as calendar:
            providers.return_value.call.side_effect = provider
            try:
                with self.assertRaises(ConversationPending):
                    self.service.dispatch('event', body)
                self.assertTrue(started.wait(2))
                self.assertIn('help', self.service.dispatch('help', self.body('help', 'help')).lower())
                reply = self.service.dispatch('cancel', self.body('cancel this request', 'cancel', '123.456'))
                self.assertIn('Stopped', reply)
            finally:
                release.set()
            self.assertIn('Stopped', self.run_request(body))
            outcome = self.service.capability_conversation.root/hashlib.sha256(b'event').hexdigest()/'outcome.json'
            for _ in range(600):
                if outcome.exists():
                    break
                time.sleep(.005)
            self.assertTrue(outcome.exists())
            calendar.assert_not_called()

    def test_specialist_uses_existing_preferences_without_separate_provider(self):
        tools = SpecialistTools(self.home, owner_key(self.config))
        path = tools.path('style_assistant')
        path.parent.mkdir(parents=True)
        db = sqlite3.connect(path)
        try:
            with db:
                db.execute('CREATE TABLE notes(receipt TEXT PRIMARY KEY,note TEXT NOT NULL)')
                db.execute('INSERT INTO notes VALUES (?,?)', ('old-specialist', 'Prefers blue.'))
        finally:
            db.close()
        body = self.body('Suggest an outfit using my preferences.')
        with patch('capo.capabilities.Providers') as providers, patch('capo.team.Providers', side_effect=AssertionError('No specialist worker')):
            providers.return_value.call.side_effect = [step('specialists.read', role='style_assistant'), finish('Try your blue shirt.')]
            self.assertIn('blue shirt', self.run_request(body))
            self.assertIn('Prefers blue.', providers.return_value.call.call_args.args[1])
        self.assertEqual(SpecialistTools(self.home, 'another-owner').read('style_assistant')['preferences'], [])

    def test_preference_receipt_preserves_role_owner_and_private_permissions(self):
        tools = SpecialistTools(self.home, owner_key(self.config))
        tools.remember('style_assistant', 'Prefers blue.', 'operation')
        tools.remember('style_assistant', 'Prefers blue.', 'operation')
        with self.assertRaises(ValueError):
            tools.remember('style_assistant', 'Prefers red.', 'operation')
        self.assertEqual(tools.read('style_assistant')['preferences'], ['Prefers blue.'])
        self.assertEqual(tools.read('shopping_assistant')['preferences'], [])
        self.assertEqual(tools.path('style_assistant').stat().st_mode & 0o777, 0o600)
        with self.assertRaises(ValueError):
            tools.read('../outside')

    def test_legacy_detection_requires_actual_saved_request(self):
        self.assertFalse(legacy_conversation(self.home, 'old'))
        root = self.home/'conversation'/hashlib.sha256(b'old').hexdigest()
        root.mkdir(parents=True)
        _write(root/'started.json', {'context': {}})
        self.assertTrue(legacy_conversation(self.home, 'old'))


if __name__ == '__main__':
    unittest.main()
