import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from capo.contracts import TEXT, object_schema
from capo.gmail import GmailReadTools, body_text
from capo.research_tools import ReadTool, ReadTools, research


def tool_step(name, arguments):
    return {'action': 'tool', 'tool': name, 'arguments_json': json.dumps(arguments),
            'reply': '', 'document_title': '', 'document': ''}


def finish(reply='The requested evidence was reviewed.'):
    return {'action': 'finish', 'tool': '', 'arguments_json': '{}', 'reply': reply,
            'document_title': '', 'document': ''}


class ResearchToolsTests(unittest.TestCase):
    def test_nonmail_adapter_uses_same_loop_and_strict_arguments(self):
        callback = Mock(return_value={'text': 'Synthetic project history'})
        tools = ReadTools([ReadTool('files.read', 'Read a configured project file',
                                   object_schema({'id': TEXT}), callback)])
        with self.assertRaises(ValueError):
            tools.call('files.write', {'id': 'a'})
        with self.assertRaises(ValueError):
            tools.call('files.read', {'id': 'a', 'shell': 'something'})
        callback.assert_not_called()
        provider = Mock()
        provider.call.side_effect = [tool_step('files.read', {'id': 'a'}), finish()]
        with tempfile.TemporaryDirectory() as tmp:
            result = research(provider, tools, {'message': 'Summarize project history'}, Path(tmp))
        callback.assert_called_once_with(id='a')
        self.assertEqual(result['receipts'][0]['result']['text'], 'Synthetic project history')
        self.assertIn('Synthetic project history', provider.call.call_args.args[1])

    def test_generated_lists_and_guides_are_delivered_with_the_introduction(self):
        for title, content in [('Shopping list', '- Tofu: 500 g\n- Rice: 1 cup'),
                               ('Writing guide', 'Use concrete verbs.\n' * 300)]:
            with self.subTest(title=title), tempfile.TemporaryDirectory() as tmp:
                provider = Mock()
                provider.call.return_value = {**finish('Here is your requested document.'),
                                              'document_title': title, 'document': content}
                result = research(provider, ReadTools([]), {}, Path(tmp))
                self.assertIn(content.strip(), result['reply'])
                self.assertIn(title, result['reply'])
                self.assertEqual(result['document'], content)
                self.assertEqual(provider.call.call_count, 1)

    def test_content_already_in_reply_is_not_repeated(self):
        provider = Mock()
        provider.call.return_value = {**finish('List: apples, rice.'),
                                      'document_title': 'List', 'document': 'apples, rice.'}
        with tempfile.TemporaryDirectory() as tmp:
            result = research(provider, ReadTools([]), {}, Path(tmp))
        self.assertEqual(result['reply'], 'List: apples, rice.')

    def test_malformed_completion_repairs_without_repeating_write(self):
        callback=Mock(return_value={'saved':True})
        tools=ReadTools([ReadTool('test.save','Synthetic write',object_schema({}),callback,mutates=True)])
        provider=Mock()
        provider.call.side_effect=[tool_step('test.save',{}),
            dict(finish(''),document_title='Result',document='Saved.'),finish('Saved.')]
        with tempfile.TemporaryDirectory() as tmp:
            result=research(provider,tools,{},Path(tmp),max_calls=3)
            again=research(provider,tools,{},Path(tmp),max_calls=3)
        callback.assert_called_once()
        self.assertEqual(provider.call.call_count,3)
        self.assertEqual(result,again)
        self.assertEqual(result['reply'],'Saved.')

    def test_repeated_malformed_completion_stops_with_preserved_receipts(self):
        provider=Mock();provider.call.return_value=finish('')
        with tempfile.TemporaryDirectory() as tmp:
            result=research(provider,ReadTools([]),{},Path(tmp),max_calls=1)
        self.assertEqual(provider.call.call_count,2)
        self.assertEqual(result['status'],'partial')
        self.assertEqual(result['stop_reason'],'invalid_completion_report')
        self.assertEqual(len(result['receipts']),2)

    def test_missing_action_evidence_returns_specific_host_feedback(self):
        item={'requirement':'Do not send mail','kind':'action','status':'complete','evidence':[],'next_step':''}
        provider=Mock();provider.call.side_effect=[dict(finish(),arguments_json=json.dumps({'outcomes':[item]})),
            dict(finish('No mail sent.'),arguments_json=json.dumps({'outcomes':[dict(item,kind='answer')]}))]
        with tempfile.TemporaryDirectory() as tmp:
            result=research(provider,ReadTools([]),{},Path(tmp),max_calls=2)
        self.assertIn('Completed actions need a successful matching host mutation receipt',result['receipts'][0]['error'])
        self.assertEqual(result['status'],'reported_complete')
        self.assertEqual(provider.call.call_count,2)

    def test_transport_tail_removed_before_partial_report_is_appended(self):
        item={'requirement':'Choose a date','kind':'answer','status':'needs_input','evidence':[],'next_step':'Which date?'}
        provider=Mock();provider.call.return_value=dict(finish('Which date?</reply>\n</invoke>'),
            arguments_json=json.dumps({'outcomes':[item]}),document_title='Choices',document='**Monday or Tuesday**</document>\n</invoke>')
        with tempfile.TemporaryDirectory() as tmp:
            result=research(provider,ReadTools([]),{},Path(tmp))
        self.assertNotIn('</',result['reply'])
        self.assertEqual(result['document'],'**Monday or Tuesday**')
        self.assertIn('Still to address:',result['reply'])

    def test_failed_tools_are_redacted_and_budgeted(self):
        callback = Mock(side_effect=RuntimeError('SECRET_TOKEN'))
        tools = ReadTools([ReadTool('test.read', 'Synthetic', object_schema({}), callback)])
        provider = Mock()
        provider.call.return_value = tool_step('test.read', {})
        with tempfile.TemporaryDirectory() as tmp:
            result = research(provider, tools, {}, Path(tmp), max_calls=2)
        self.assertEqual(callback.call_count, 2)
        self.assertEqual(provider.call.call_count, 3)
        self.assertIn('limit', result['reply'])
        self.assertNotIn('SECRET_TOKEN', json.dumps(result))
        self.assertNotIn('SECRET_TOKEN', str(provider.call.call_args_list))

    def test_model_cannot_select_unregistered_write_tool(self):
        callback = Mock()
        tools = ReadTools([ReadTool('mail.read', 'Read', object_schema({}), callback)])
        provider = Mock()
        provider.call.side_effect = [tool_step('mail.send', {}), finish('Sending is unavailable.')]
        with tempfile.TemporaryDirectory() as tmp:
            result = research(provider, tools, {}, Path(tmp))
        callback.assert_not_called()
        self.assertIn('error', result['receipts'][0])

    def test_pagination_preserves_query_and_returns_actual_cursor(self):
        client = Mock()
        client.get.side_effect = [
            {'messages': [{'id': 'a'}], 'nextPageToken': 'cursor'},
            {'messages': [{'id': 'b'}]},
        ]
        tools = GmailReadTools(client)
        page = tools.call('mail.search', {'query': 'in:sent', 'page_size': '1', 'page_token': ''})
        self.assertTrue(page['more_available'])
        with self.assertRaises(ValueError):
            tools.call('mail.search', {'query': 'in:inbox', 'page_size': '1', 'page_token': 'cursor'})
        tools.call('mail.search', {'query': 'in:sent', 'page_size': '1', 'page_token': 'cursor'})
        self.assertEqual(client.get.call_args.args[1]['pageToken'], 'cursor')
        self.assertEqual(tools.known_ids, {'a', 'b'})

    def test_read_only_discovered_ids_with_partial_failure_and_quote_choice(self):
        client = Mock()
        client.get.side_effect = [
            {'messages': [{'id': 'a'}, {'id': 'b'}]},
            {'payload': {'mimeType': 'text/plain', 'body': {'data':
                base64.urlsafe_b64encode(b'Own prose\n> Quoted reply').decode()}}},
            RuntimeError('PRIVATE_FAILURE_BODY'),
        ]
        tools = GmailReadTools(client)
        tools.search('in:sent', '2', '')
        with self.assertRaises(ValueError):
            tools.read(['invented'], False)
        result = tools.read(['a', 'b'], False)
        self.assertIn('Quoted reply', result['messages'][0]['body'])
        self.assertEqual(result['errors'][0]['id'], 'b')
        self.assertEqual(tools.read_attempts, 2)
        self.assertNotIn('PRIVATE_FAILURE_BODY', json.dumps(result))

    def test_read_budget_and_truncation(self):
        client = Mock()
        client.get.return_value = {'payload': {'mimeType': 'text/plain', 'body': {'data':
            base64.urlsafe_b64encode(b'x'*8000).decode()}}}
        tools = GmailReadTools(client)
        tools.known_ids = {'a'}
        tools.characters = 119990
        result = tools.read(['a'], False)
        self.assertEqual(len(result['messages'][0]['body']), 10)
        self.assertTrue(result['messages'][0]['truncated'])
        with self.assertRaises(ValueError):
            tools.read(['a'], False)
        tools.characters = 0
        tools.read_attempts = 50
        with self.assertRaises(ValueError):
            tools.read(['a'], False)
        self.assertEqual(client.get.call_count, 1)

    def test_html_quotes_can_be_preserved_for_other_tasks(self):
        part = {'mimeType': 'text/html', 'body': {'data': base64.urlsafe_b64encode(
            b'<p>Own prose</p><blockquote>Previous message</blockquote>').decode()}}
        self.assertIn('Previous message', body_text(part, strip_quotes=False))
        self.assertNotIn('Previous message', body_text(part, strip_quotes=True))
