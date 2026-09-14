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
