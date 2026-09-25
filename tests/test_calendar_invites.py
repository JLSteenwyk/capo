import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from capo.contacts import Contacts
from capo.calendar_invites import Invitations
from capo.calendar_actions import CalendarActions
from capo.calendar_tools import CalendarTools
from capo.calendar import event_body
from capo.effects import UncertainEffect
from capo.research_tools import ToolInputError
from test_calendar_actions import arguments


class ContactInviteTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.home=Path(tmp.name)
        self.contacts=Contacts(self.home,'owner')
        self.contact=self.contacts.save('Alex','Alex@example.invalid','save')['contact']
        self.event={'id':'event','etag':'v1','organizer':{'self':True},'summary':'Dinner',
                    'description':'Keep notes','attendees':[{'email':'existing@example.invalid','responseStatus':'accepted'}]}
        self.cache={'event':copy.deepcopy(self.event)}
        self.client=Mock();self.client.lookup.side_effect=lambda _:copy.deepcopy(self.event)
        def request(method,event_id,**kwargs):
            self.event.update(copy.deepcopy(kwargs['json']));return copy.deepcopy(self.event)
        self.client.request.side_effect=request
        patcher=patch('capo.calendar_invites.GoogleCalendar',return_value=self.client)
        patcher.start();self.addCleanup(patcher.stop)
        self.adapter=Invitations(self.home,'owner','primary',self.cache)

    def test_contacts_private_persistent_ambiguous_and_replay_safe(self):
        self.contacts.save('Alex','other@example.invalid','second')
        self.assertEqual(len(Contacts(self.home,'owner').search('alex')['contacts']),2)
        self.assertEqual(Contacts(self.home,'different').search('')['contacts'],[])
        self.assertEqual(self.contacts.path.stat().st_mode & 0o777,0o600)
        self.contacts.save('Updated','alex@example.invalid','rename')
        self.contacts.save('Alex','Alex@example.invalid','save')
        self.assertEqual(self.contacts.search('alex@example.invalid')['contacts'][0]['name'],'Updated')
        self.contacts.remove(self.contact['id'],'remove')
        with self.assertRaises(ToolInputError):self.contacts.resolve([self.contact['id']])
        with self.assertRaises(ToolInputError):self.contacts.save('Other','different@example.invalid','save')
        for invalid in ('not an email','a..b@example.invalid','a@-bad.invalid','a@bad..invalid','a@example.invalid\nBcc:evil'):
            with self.assertRaises(ToolInputError):self.contacts.save('Invalid',invalid,'bad')

    def test_add_preserves_guests_and_metadata_without_resending(self):
        result=self.adapter.invite('event',self.contacts.resolve([self.contact['id']]),'invite')
        self.assertTrue(result['verified'])
        kwargs=self.client.request.call_args.kwargs
        self.assertEqual(set(kwargs['json']),{'attendees'})
        self.assertEqual(kwargs['headers'],{'If-Match':'v1'})
        self.assertEqual(kwargs['params'],{'sendUpdates':'all'})
        self.assertEqual(self.event['attendees'][0]['responseStatus'],'accepted')
        self.assertEqual(self.event['description'],'Keep notes')
        self.adapter.invite('event',['alex@example.invalid'],'retry')
        self.assertEqual(self.client.request.call_count,1)

    def test_lost_response_reconciles_readonly_across_restart(self):
        original=self.client.request.side_effect
        def lost(*args,**kwargs):original(*args,**kwargs);raise TimeoutError()
        self.client.request.side_effect=lost
        with self.assertRaises(TimeoutError):self.adapter.invite('event',['alex@example.invalid'],'invite')
        restarted=Invitations(self.home,'owner','primary',{})
        self.assertTrue(restarted.invite('event',['alex@example.invalid'],'retry')['reconciled'])
        self.assertEqual(self.client.request.call_count,1)

    def test_unknown_result_lists_and_reconciles_without_write(self):
        self.client.request.side_effect=TimeoutError()
        with self.assertRaises(TimeoutError):self.adapter.invite('event',['alex@example.invalid'],'invite')
        with self.assertRaises(UncertainEffect):self.adapter.invite('event',['alex@example.invalid'],'retry')
        actions=CalendarActions(self.home,'owner','America/Los_Angeles',{})
        pending=actions.pending()['actions'];self.assertEqual(pending[0]['action'],'invite')
        with patch('capo.calendar_actions.GoogleCalendar',return_value=self.client):
            self.assertFalse(actions.reconcile(pending[0]['id'])['confirmed'])
            self.event['attendees'].append({'email':'alex@example.invalid'})
            self.assertTrue(actions.reconcile(pending[0]['id'])['verified'])
        self.assertEqual(self.client.request.call_count,1)

    def test_guard_rejections_never_send(self):
        for change in ({'etag':'v2'},{'organizer':{'self':False}},{'attendeesOmitted':True},
                       {'recurrence':['RRULE:FREQ=WEEKLY']},{'recurringEventId':'series'},{'eventType':'birthday'}):
            with self.subTest(change=change):
                old=copy.deepcopy(self.event);self.event.update(change)
                with self.assertRaises(ToolInputError):self.adapter.invite('event',['alex@example.invalid'],'blocked')
                self.event=old
        self.client.request.assert_not_called()

    def test_saved_contact_composes_with_new_event_and_series(self):
        tools=CalendarTools('America/Los_Angeles',lambda *_:self.client)
        change=tools.action_tools(self.home,'owner')[0].execute
        self.client.events.return_value=[]
        def create(method, *args, **kwargs):
            self.created=kwargs['json'];return self.created
        self.client.request.side_effect=create
        self.client.lookup.side_effect=lambda _:self.created
        with patch('capo.calendar_actions.GoogleCalendar',return_value=self.client):
            result=change(**arguments(),recurrence='RRULE:FREQ=WEEKLY;COUNT=3',
                          contact_ids=[self.contact['id']],operation_id='series')
        self.assertTrue(result['verified'])
        self.assertEqual(self.created['attendees'],[{'email':'alex@example.invalid'}])
        self.assertEqual(self.client.request.call_args.kwargs['params']['sendUpdates'],'all')
        self.assertEqual(self.created['recurrence'],['RRULE:COUNT=3;FREQ=WEEKLY'])

    def test_different_guests_do_not_create_duplicate_event(self):
        self.client.events.return_value=[dict(event_body(arguments(),'America/Los_Angeles'),id='existing')]
        with patch('capo.calendar_actions.GoogleCalendar',return_value=self.client):
            with self.assertRaises(ToolInputError):
                CalendarActions(self.home,'owner','America/Los_Angeles',{}).change(
                    **arguments(),attendees=['alex@example.invalid'],operation_id='create')
        self.client.request.assert_not_called()

    def test_invite_tool_resolves_contacts_and_blocks_unowned_calendar(self):
        tools=CalendarTools('America/Los_Angeles',lambda *_:self.client)
        tools.primary_cache.update(self.cache)
        tools.calendars['shared']={'id':'shared','accessRole':'writer'}
        invite=next(t for t in tools.action_tools(self.home,'owner') if t.name=='calendar.invite').execute
        with self.assertRaises(PermissionError):
            invite(calendar_id='shared',event_id='event',contact_ids=[self.contact['id']],operation_id='foreign')
        with self.assertRaises(ToolInputError):
            invite(calendar_id='primary',event_id='event',contact_ids=['unknown'],operation_id='unknown')
        self.client.request.assert_not_called()
        result=invite(calendar_id='primary',event_id='event',contact_ids=[self.contact['id']],operation_id='valid')
        self.assertTrue(result['verified'])
