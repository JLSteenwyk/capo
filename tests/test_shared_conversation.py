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

    def test_owner_correction_during_reasoning_stops_old_tool(self):
        from capo.slack import ingest
        original=self.body('Check tomorrow morning and make the requested reminder.')
        correction={'team_id':'T123','event_id':'correction','event':{'type':'message',
            'channel':'C123','user':'U123','ts':'125.456','thread_ts':'123.456',
            'text':'Actually, just tell me what is planned; do not create anything.'}}
        with patch('capo.capabilities.Providers') as providers,patch('capo.calendar.GoogleCalendar') as calendar:
            def decide(*args,**kwargs):
                if providers.return_value.call.call_count==1:
                    self.assertTrue(ingest(self.home,self.config,correction))
                    return step('calendar.events',start='2026-11-02T00:00:00-08:00',end='2026-11-03T00:00:00-08:00')
                return finish('I will only check the plan.')
            providers.return_value.call.side_effect=decide
            self.assertIn('received your update',self.run_request(original))
            calendar.return_value.events.assert_not_called()
            self.assertIn('only check',self.run_request(correction))
            prompt=providers.return_value.call.call_args.args[1]
            data=json.loads(prompt.splitlines()[-1])
            self.assertIn('make the requested reminder',data['request']['request_state']['original_request']['message'])
            self.assertIn('do not create anything',data['request']['message'])

    def test_update_monitor_ignores_other_owner_threads_and_status(self):
        from capo.request_updates import latest
        original=self.body('Do the requested work.')
        context={'request_event':original['event_id'],'request_thread':'123.456'}
        status=self.body('status','status','123.456')
        self.assertIsNone(latest(self.home,self.config,context))
        other=self.body('Change the work','other','999.999')
        self.assertIsNone(latest(self.home,self.config,context))
        stranger={'team_id':'T123','event_id':'stranger','event':dict(status['event'],user='U999',text='Stop')}
        self.store.enqueue_slack('stranger',stranger)
        self.assertIsNone(latest(self.home,self.config,context))
        self.body('Use the new date','correction','123.456')
        self.assertEqual(latest(self.home,self.config,context),'correction')

    def test_followup_updates_known_event_without_repeating_search(self):
        from capo.evaluations.calendar_cases import CalendarWorld
        import copy
        world = CalendarWorld({})
        original = copy.deepcopy(world.calendars)
        body = self.body('Change the location of my previously created Planning event to Room 9. Its primary-calendar ID is planning; keep the time and notes.',
                         'location-followup')
        with patch('capo.capabilities.Providers') as providers, \
                patch('capo.calendar.GoogleCalendar', side_effect=world.client), \
                patch('capo.calendar_actions.GoogleCalendar', side_effect=world.client):
            done = finish('Updated the location to Room 9; the time and notes are unchanged.')
            done['arguments_json'] = json.dumps({'outcomes':[dict(requirement='Update location',kind='action',
                status='complete',evidence=['1'],next_step='')]})
            providers.return_value.call.side_effect = [step('calendar.event',calendar_id='primary',event_id='planning'),
                step('calendar.change',action='update',calendar_id='primary',event_id='planning',title='Planning',
                     location='Room 9',start='2030-11-04T09:00:00-08:00',end='2030-11-04T10:00:00-08:00',all_day=False),done]
            self.assertIn('Room 9',self.run_request(body))
            row = world.calendars['primary']['planning']
            self.assertEqual(row['location'],'Room 9')
            self.assertEqual(row['description'],original['primary']['planning']['description'])
            self.assertEqual(row['start']['dateTime'],original['primary']['planning']['start']['dateTime'])
            self.assertEqual(world.calendars['work'],original['work'])
            self.assertEqual(len(world.writes),1)
            restarted = SlackService(self.store,self.config,Mock())
            self.run_request(body,restarted)
            self.assertEqual(len(world.writes),1)

    def test_new_followup_creates_every_block_after_legacy_single_event(self):
        from capo.evaluations.calendar_cases import event
        from capo.evaluations.research_cases import ResearchWorld
        import copy
        world = ResearchWorld({})
        world.calendars['primary'] = {'overview': event('overview', 'Conference overview', 8),
                                      'breakfast': event('breakfast', 'Breakfast', 9)}
        preserved = copy.deepcopy(world.calendars)
        self.body('Keep my overview and add each agenda block.', 'old-event')
        key = hashlib.sha256(b'old-event').hexdigest()
        legacy = self.home / 'conversation' / key
        legacy.mkdir(parents=True)
        _write(legacy / 'started.json', {'legacy': True})
        body = self.body('Add all blocks on November 4, 2030: Breakfast 9–10, Keynote 10–11, Workshop 11–12. Keep the overview.',
                         'new-event', '123.456')
        body['event'].update(type='message', text=body['event']['text'].replace('<@UBOT> ', ''))
        self.store.db.execute('UPDATE slack_inbox SET data=? WHERE id=?', (json.dumps(body), 'new-event'))
        self.store.db.commit()
        with patch('capo.capabilities.Providers') as providers, \
                patch('capo.calendar.GoogleCalendar', side_effect=world.client), \
                patch('capo.calendar_actions.GoogleCalendar', side_effect=world.client), \
                patch('capo.conversation.Providers', side_effect=AssertionError('Must not use single-event classifier')):
            actions = [step('calendar.change', action='create', event_id='', title=title, location='Room 4',
                            start=f'2030-11-04T{hour:02}:00:00-08:00', end=f'2030-11-04T{hour+1:02}:00:00-08:00', all_day=False)
                       for title, hour in [('Breakfast', 9), ('Keynote', 10), ('Workshop', 11)]]
            done = finish('Added Keynote and Workshop. Breakfast and the overview were already present and unchanged.')
            done['arguments_json'] = json.dumps({'outcomes': [dict(requirement=title, kind='action', status='complete',
                evidence=[str(i)], next_step='') for i, title in enumerate(['Breakfast', 'Keynote', 'Workshop'])]})
            providers.return_value.call.side_effect = actions + [done]
            response = self.run_request(body)
            self.assertIn('Workshop', response)
            self.assertEqual(len(world.writes), 2)
            self.assertEqual({v['summary'] for v in world.calendars['primary'].values()},
                             {'Conference overview', 'Breakfast', 'Keynote', 'Workshop'})
            for id in ['overview', 'breakfast']:
                self.assertEqual(world.calendars['primary'][id], preserved['primary'][id])
            self.assertEqual(world.calendars['work'], preserved['work'])
            restarted = SlackService(self.store, self.config, Mock())
            self.assertEqual(self.run_request(body, restarted), response)
            self.assertEqual(len(world.writes), 2)

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

    def test_digest_thread_can_use_calendar_without_feedback_classifier(self):
        from datetime import datetime, timezone
        from capo.digest import DigestStore, scope
        db=DigestStore(self.home)
        try:
            before=db.preferences(scope(self.config))
            db.save({'key':'digest-fixture','scope':scope(self.config),'day':'2026-11-02',
                     'status':'sent','ts':'100.1','payload':{'text':'Morning briefing','news':[]}},
                    datetime.now(timezone.utc))
        finally:db.close()
        body=self.body('What is on my calendar?', 'digest-reply', '100.1')
        body['event'].update(type='message',text='What is on my calendar?')
        with patch('capo.capabilities.Providers') as providers, patch('capo.calendar.GoogleCalendar') as calendar, \
                patch('capo.digest_feedback.FeedbackConversation',side_effect=AssertionError('No digest-only classifier')):
            calendar.return_value.events.return_value=[]
            providers.return_value.call.side_effect=[step('digest.read'),
                step('calendar.events',start='2026-11-02T00:00:00-08:00',end='2026-11-03T00:00:00-08:00'),
                finish('No events were found on your primary calendar for that day.')]
            self.assertIn('No events',self.run_request(body))
            calendar.return_value.events.assert_called_once()
        db=DigestStore(self.home)
        try:self.assertEqual(db.preferences(scope(self.config)),before)
        finally:db.close()


if __name__ == '__main__':
    unittest.main()
