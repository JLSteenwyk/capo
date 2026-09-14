import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from capo.contracts import object_schema
from capo.providers import run_cli, WorkerError, AuthenticationError
from capo.recovery import RateLimited, RetryLater, failure_summary
from capo.research_tools import ReadTools, ReadTool, research
from capo.service_errors import check


class ServiceErrorTests(unittest.TestCase):
    def test_retry_after_auth_and_permission_are_classified_without_private_body(self):
        with patch('capo.service_errors.time.time', return_value=100):
            with self.assertRaises(RateLimited) as exc:
                check(SimpleNamespace(status_code=429, headers={'Retry-After':'120'}), 'Gmail')
            self.assertEqual(exc.exception.reset_at, 220)
        with self.assertRaises(AuthenticationError) as exc:
            check(SimpleNamespace(status_code=401), 'Gmail')
        self.assertIn('Gmail', failure_summary(exc.exception))
        with self.assertRaises(PermissionError):
            check(SimpleNamespace(status_code=403), 'Calendar')
        with self.assertRaises(ConnectionError):
            check(SimpleNamespace(status_code=503), 'Calendar')

    def test_draft_write_and_reconciliation_preserve_service_errors(self):
        from capo.gmail import Gmail
        client = Gmail.__new__(Gmail)
        client.drafts_enabled = True
        client.session = Mock()
        client.session.request.return_value = SimpleNamespace(status_code=429, headers={'Retry-After':'120'})
        with self.assertRaises(RateLimited):
            client.draft_write('POST', payload={'message':{}})
        self.assertEqual(client.session.request.call_count, 1)
        client.session.get.return_value = SimpleNamespace(status_code=401)
        with self.assertRaises(AuthenticationError):
            client.draft_exists('draft123')
        client.session.get.return_value = SimpleNamespace(status_code=404)
        self.assertFalse(client.draft_exists('draft123'))

    def test_nonzero_cli_exit_retains_structured_rate_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'stdout.txt').write_text(json.dumps({'type':'error', 'error':{
                'status':429,'reset_at':500,'message':'private diagnostic'}}))
            with patch('capo.providers.run_process', side_effect=WorkerError('exit 1')):
                with self.assertRaises(RateLimited) as exc:
                    run_cli('Codex', ['codex'], root, root, 1)
            self.assertEqual(exc.exception.reset_at, 500)
            self.assertNotIn('private', str(exc.exception))

    def test_tool_rate_limit_pauses_whole_checkpoint_until_reset(self):
        tool = {'action':'tool','tool':'source.read','arguments_json':'{}','reply':'','document_title':'','document':''}
        finish = {'action':'finish','tool':'','arguments_json':'{}','reply':'The source remains unavailable.','document_title':'','document':''}
        provider = Mock()
        provider.call.side_effect = [tool, finish]
        read = Mock(side_effect=RateLimited(200))
        tools = ReadTools([ReadTool('source.read','Read source',object_schema({}),read)])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch('time.time', return_value=100):
                with self.assertRaises(RetryLater):
                    research(provider, tools, {}, root)
                with self.assertRaises(RetryLater):
                    research(provider, tools, {}, root)
            self.assertEqual(read.call_count, 1)
            self.assertEqual(provider.call.call_count, 1)
            with patch('time.time', return_value=300):
                result = research(provider, tools, {}, root)
            self.assertEqual(result['reply'], finish['reply'])
            self.assertEqual(len(result['receipts']), 1)
