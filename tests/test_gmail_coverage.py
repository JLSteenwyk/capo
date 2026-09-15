"""Bounded mail inspection preserves useful evidence and reports missing coverage."""
import base64
import json
import unittest
from unittest.mock import Mock

from capo.gmail import GmailReadTools
from capo.providers import ServiceAuthenticationError


def message(text='Current prose', labels=None):
    return {'labelIds': labels or [], 'payload': {'mimeType': 'text/plain',
        'body': {'data': base64.urlsafe_b64encode(text.encode()).decode()}}}


class MailCoverageTests(unittest.TestCase):
    def test_thread_preserves_remaining_budget_and_discloses_unread(self):
        client = Mock()
        client.get.side_effect = [{'messages': [{'id': str(i)} for i in range(40)]},
                                  message(), message()]
        tools = GmailReadTools(client)
        tools.known_threads.add('thread')
        tools.read_attempts = 48
        result = tools.thread('thread')
        self.assertEqual([m['id'] for m in result['pages'][0]['messages']], ['0', '1'])
        self.assertEqual(result['unread_ids'], [str(i) for i in range(2, 40)])
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(client.get.call_count, 3)
        self.assertEqual(tools.read_attempts, 50)

    def test_access_failure_retains_previous_message_and_stops_reads(self):
        client = Mock()
        client.get.side_effect = [message(labels=['SENT']),
                                  ServiceAuthenticationError('PRIVATE_DETAIL')]
        tools = GmailReadTools(client)
        tools.known_ids.update(['a', 'b', 'c'])
        result = tools.read(['a', 'b', 'c'], False)
        self.assertEqual(result['messages'][0]['id'], 'a')
        self.assertEqual(result['errors'][0]['category'], 'authentication_required')
        self.assertEqual(result['unread_ids'], ['c'])
        self.assertEqual(result['status'], 'partial')
        self.assertNotIn('PRIVATE_DETAIL', json.dumps(result))
        self.assertIn('candidate', result['messages'][0]['authorship'])
        self.assertEqual(client.get.call_count, 2)

    def test_quote_filter_does_not_hide_source_truncation(self):
        client = Mock()
        client.get.return_value = message('Own prose\nOn Monday wrote:\n' + 'q'*13000)
        tools = GmailReadTools(client)
        tools.known_ids.add('a')
        result = tools.read(['a'], True)
        self.assertEqual(result['messages'][0]['body'], 'Own prose')
        self.assertTrue(result['messages'][0]['truncated'])
        self.assertIn('not established', result['messages'][0]['authorship'])


if __name__ == '__main__':
    unittest.main()
