import unittest
from unittest.mock import Mock
from capo.gmail import Gmail, SCOPES

class GmailTests(unittest.TestCase):
    def test_inbox_only_reads_bounded_metadata_and_reports_partial_coverage(self):
        client=Gmail.__new__(Gmail)
        client.session=Mock()
        page=Mock(ok=True);page.json.return_value={'messages':[{'id':'abc'}], 'nextPageToken':'more'}
        message=Mock(ok=True);message.json.return_value={'payload':{'headers':[
            {'name':'Subject','value':'Synthetic request'}, {'name':'From','value':'test@example.invalid'}]},
            'snippet':'Please review this.', 'labelIds':['UNREAD']}
        client.session.get.side_effect=[page,message]
        result=client.inbox()
        self.assertTrue(result['more_available'])
        self.assertEqual(result['messages'][0]['subject'],'Synthetic request')
        self.assertTrue(result['messages'][0]['unread'])
        self.assertEqual(client.session.get.call_args.kwargs['params']['format'],'metadata')
        self.assertEqual(len(client.session.method_calls),2)
        self.assertEqual(SCOPES,['https://www.googleapis.com/auth/gmail.readonly'])

    def test_api_error_does_not_expose_response(self):
        client=Gmail.__new__(Gmail);client.session=Mock()
        client.session.get.return_value=Mock(ok=False,text='SECRET')
        with self.assertRaises(RuntimeError) as error:client.inbox()
        self.assertNotIn('SECRET',str(error.exception))

    def test_full_sent_messages_strip_quotes_and_skip_attachments(self):
        import base64
        client=Gmail.__new__(Gmail)
        text='Hey!\nThanks for the update.\nOn Monday, Someone wrote:\nOther person text'
        part={'mimeType':'text/plain','body':{'data':base64.urlsafe_b64encode(text.encode()).decode()}}
        attachment={'mimeType':'text/plain','filename':'private.txt','body':{'data':base64.urlsafe_b64encode(b'attachment').decode()}}
        client.get=Mock(side_effect=[{'messages':[{'id':'one'}]}, {'payload':{'parts':[part,attachment]},'id':'one'}])
        result=client.messages('in:sent',25)
        self.assertIn('Thanks',result['messages'][0]['body'])
        self.assertNotIn('Other person',result['messages'][0]['body'])
        self.assertNotIn('attachment',result['messages'][0]['body'])
        self.assertEqual(client.get.call_args_list[0].args[1]['q'],'in:sent')
        self.assertEqual(client.get.call_args_list[1].args[1]['format'],'full')

    def test_style_request_reads_sent_mail_and_saves_private_guide(self):
        import tempfile,time,json
        from pathlib import Path
        from unittest.mock import patch
        from capo.gmail import InboxConversation
        from capo.conversation import ConversationPending
        with tempfile.TemporaryDirectory() as tmp, patch('capo.gmail.Providers') as provider, patch('capo.gmail.Gmail') as gmail:
            provider.return_value.call.side_effect=[{'action':'writing_style','query':'irrelevant','limit':'25'},
                {'reply':'Your writing is concise.','guide':'Use short sentences.'}]
            gmail.return_value.messages.return_value={'messages':[{'body':'Thanks!'}], 'more_available':False}
            router=InboxConversation(Path(tmp))
            for _ in range(300):
                try:
                    result=router.poll('style',{'aliases':[],'objectives':[],'message':'Learn my writing style.'});break
                except ConversationPending:time.sleep(.005)
            else:self.fail('Style request did not finish')
            self.assertIn('concise',result['reply'])
            gmail.return_value.messages.assert_called_once_with('in:sent',25)
            gmail.return_value.inbox.assert_not_called()
            p=router.root.parent/'writing-guide.json'
            self.assertEqual(json.loads(p.read_text())['sample_count'],1)
            self.assertEqual(p.stat().st_mode & 0o777,0o600)
