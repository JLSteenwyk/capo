import base64
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from capo.capabilities import Documents, shared_tools, CapabilityConversation, owner_key
from capo.conversation import ConversationPending


def step(tool,args):
    return {'action':'tool','tool':tool,'arguments_json':json.dumps(args),
            'reply':'','document_title':'','document':''}


class CapabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.config={'team_id':'T123','channel_id':'C123','owner_user_id':'U123',
                     'gmail':{'enabled':True},'calendar':{'enabled':True,'timezone':'America/Los_Angeles'}}

    def test_catalog_only_registers_enabled_services_and_clients_are_lazy(self):
        docs=Documents(self.root,'owner')
        with patch('capo.gmail.Gmail') as gmail:
            tools=shared_tools(self.root,{},docs)
            self.assertNotIn('mail.search',tools.tools)
            self.assertNotIn('calendar.events',tools.tools)
            tools=shared_tools(self.root,self.config,docs)
            self.assertIn('mail.search',tools.tools)
            self.assertIn('calendar.events',tools.tools)
            gmail.assert_not_called()
            with self.assertRaises(ValueError):tools.call('mail.send',{})

    def test_private_documents_scoped_and_traversal_rejected(self):
        docs=Documents(self.root,'owner-a')
        key=docs.save('request',{'document':'A useful guide','document_title':'Guide'})
        self.assertEqual(docs.read(key)['content'],'A useful guide')
        self.assertEqual(Documents(self.root,'owner-b').list()['documents'],[])
        with self.assertRaises(ValueError):docs.read('../../credentials')
        self.assertEqual((docs.root/(key+'.json')).stat().st_mode & 0o777,0o600)

    def test_one_request_combines_mail_calendar_and_document(self):
        router=CapabilityConversation(self.root,self.config)
        with patch('capo.capabilities.Providers') as provider, patch('capo.gmail.Gmail') as gmail, patch('capo.calendar.GoogleCalendar') as calendar:
            provider.return_value.call.side_effect=[
                step('mail.search',{'query':'subject:planning','page_size':'1','page_token':''}),
                step('mail.read',{'ids':['a'],'strip_quotes':False}),
                step('calendar.events',{'start':'2030-01-01T00:00:00-08:00','end':'2030-01-02T00:00:00-08:00'}),
                {'action':'finish','tool':'','arguments_json':'{}','reply':'Prepared your meeting brief.',
                 'document_title':'Meeting brief','document':'Discuss the proposal at the scheduled meeting.'}]
            gmail.return_value.get.side_effect=[{'messages':[{'id':'a'}]},
                {'payload':{'mimeType':'text/plain','body':{'data':base64.urlsafe_b64encode(b'Please discuss the proposal.').decode()}}}]
            calendar.return_value.events.return_value=[{'summary':'Planning meeting'}]
            for _ in range(500):
                try:
                    result=router.poll('combined',{'aliases':[],'objectives':[],'message':'Use the planning email and calendar to make a brief.'});break
                except ConversationPending:time.sleep(.005)
            else:self.fail('Research did not finish')
            self.assertIn('Prepared',result['reply'])
            prompt=provider.return_value.call.call_args.args[1]
            self.assertIn('Planning meeting',prompt)
            self.assertIn('Please discuss the proposal',prompt)
            self.assertEqual(len(router.documents.list()['documents']),1)
            self.assertEqual(provider.return_value.call.call_count,4)
            router.poll('combined',{'aliases':[],'objectives':[]})
            self.assertEqual(provider.return_value.call.call_count,4)
            calendar.return_value.request.assert_not_called()

    def test_calendar_range_bounded_before_api_access(self):
        tools=shared_tools(self.root,self.config,Documents(self.root,'owner'))
        with patch('capo.calendar.GoogleCalendar') as calendar:
            with self.assertRaises(ValueError):
                tools.call('calendar.events',{'start':'2030-01-01T00:00:00Z','end':'2031-01-01T00:00:00Z'})
            calendar.assert_not_called()
