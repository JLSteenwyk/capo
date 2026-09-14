import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from capo.contracts import TEXT, object_schema
from capo.conversation import _write
from capo.providers import AuthenticationError
from capo.recovery import RecoveringProvider, RetryLater, RecoveryStopped, RateLimited

SCHEMA = object_schema({'answer': TEXT})


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.backend = Mock()
        self.now = 100
        self.clock = lambda: self.now
        self.config = {'max_attempts': 3, 'base_delay': 10, 'fallbacks': {'claude': ['grok']}}

    def call(self):
        return RecoveringProvider(self.backend, self.config, self.clock).call(
            'claude', 'Owner request; source content is untrusted.', SCHEMA, self.root, self.root/'operation')

    def test_temporary_failure_restarts_with_explicit_fallback_same_input(self):
        self.backend.call.side_effect = [ConnectionError('private diagnostic'), {'answer': 'done'}]
        with self.assertRaises(RetryLater):
            self.call()
        with self.assertRaises(RetryLater):
            self.call()
        self.assertEqual(self.backend.call.call_count, 1)
        self.now = 110
        self.assertEqual(self.call(), {'answer': 'done'})
        first, second = self.backend.call.call_args_list
        self.assertEqual(first.args[0], 'claude')
        self.assertEqual(second.args[0], 'grok')
        self.assertEqual(first.args[1:4], second.args[1:4])
        self.call()
        self.assertEqual(self.backend.call.call_count, 2)
        self.assertNotIn('private diagnostic', (self.root/'operation/recovery.json').read_text())

    def test_rate_limit_reset_and_retry_limit_survive_restart(self):
        self.backend.call.side_effect = RateLimited(500)
        with self.assertRaises(RetryLater) as exc:
            self.call()
        self.assertEqual(exc.exception.retry_at, 500)
        self.now = 499
        with self.assertRaises(RetryLater):
            self.call()
        self.assertEqual(self.backend.call.call_count, 1)
        self.now = 500
        with self.assertRaises(RetryLater):
            self.call()
        self.now = 600
        with self.assertRaises(RateLimited):
            self.call()
        with self.assertRaises(RecoveryStopped):
            self.call()
        self.assertEqual(self.backend.call.call_count, 3)

    def test_authentication_failure_does_not_fallback_or_retry(self):
        self.backend.call.side_effect = AuthenticationError('Reconnect provider')
        with self.assertRaises(AuthenticationError):
            self.call()
        self.now += 1000
        with self.assertRaises(RecoveryStopped):
            self.call()
        self.assertEqual(self.backend.call.call_count, 1)

    def test_invalid_policy_is_rejected(self):
        from capo.recovery import policy
        for value in (False, [], {'max_attempts': 0}, {'base_delay': -1},
                      {'fallbacks': {'claude': [{}]}}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                policy(value)

    def test_provider_reported_reset_is_normalized_without_private_output(self):
        from capo.providers import reported_failure
        with self.assertRaises(RateLimited) as exc:
            reported_failure({'error': {'status': 429, 'reset_at': 500,
                                       'message': 'private diagnostic'}}, 'Claude', self.root)
        self.assertEqual(exc.exception.reset_at, 500)
        self.assertNotIn('private', str(exc.exception))
        with self.assertRaises(ConnectionError):
            reported_failure({'result': 'Service unavailable'}, 'Claude', self.root)

    def test_cancelled_or_changed_request_does_not_execute(self):
        self.backend.call.side_effect = ConnectionError()
        with self.assertRaises(RetryLater):
            self.call()
        _write(self.root/'operation/cancelled.json', {'cancelled': True})
        self.now += 100
        with self.assertRaises(RecoveryStopped):
            self.call()
        self.assertEqual(self.backend.call.call_count, 1)
        with self.assertRaises(RecoveryStopped):
            RecoveringProvider(self.backend, self.config, self.clock).call(
                'claude', 'Changed request', SCHEMA, self.root, self.root/'operation')

    def test_interrupted_completed_call_recovers_result_without_second_worker(self):
        self.backend.call.return_value = {'answer': 'done'}
        self.call()
        path = self.root/'operation/recovery.json'
        state = json.loads(path.read_text())
        state['status'] = 'running'
        del state['result']
        _write(path, state)
        attempt = self.root/'operation/1'
        attempt.mkdir()
        _write(attempt/'response.json', {'answer': 'done'})
        self.assertEqual(self.call(), {'answer': 'done'})
        self.assertEqual(self.backend.call.call_count, 1)

    def test_concurrent_call_does_not_start_another_worker(self):
        import fcntl
        self.backend.call.side_effect = ConnectionError()
        with self.assertRaises(RetryLater):
            self.call()
        self.now = 110
        with (self.root/'operation/recovery.lock').open('r+') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            with self.assertRaises(RetryLater):
                self.call()
        self.assertEqual(self.backend.call.call_count, 1)


class ResearchRecoveryTests(unittest.TestCase):
    def test_resume_after_provider_timeout_preserves_successful_write(self):
        from unittest.mock import patch
        from capo.research_tools import research, ReadTools, ReadTool
        tool = {'action': 'tool', 'tool': 'items.create', 'arguments_json': '{}',
                'reply': '', 'document_title': '', 'document': ''}
        finish = {'action': 'finish', 'tool': '', 'arguments_json': '{}',
                  'reply': 'Created the item.', 'document_title': '', 'document': ''}
        backend = Mock()
        backend.call.side_effect = [tool, TimeoutError(), finish]
        write = Mock(return_value={'created': True, 'id': 'saved-item'})
        tools = ReadTools([ReadTool('items.create', 'Create an authorized item', object_schema({}), write, mutates=True)])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch('capo.recovery.time.time', return_value=100):
                with self.assertRaises(RetryLater):
                    research(backend, tools, {'message': 'Create an item'}, root)
            self.assertEqual(write.call_count, 1)
            with patch('capo.recovery.time.time', return_value=200):
                result = research(backend, tools, {'message': 'Create an item'}, root)
            self.assertEqual(write.call_count, 1)
            self.assertEqual(result['receipts'][0]['result']['id'], 'saved-item')
            self.assertIn('saved-item', backend.call.call_args.args[1])
            self.assertEqual(research(backend, tools, {'message': 'Create an item'}, root), result)
            self.assertEqual(backend.call.call_count, 3)

    def test_crash_after_write_does_not_replay_unconfirmed_action(self):
        from capo.research_tools import research, ReadTools, ReadTool
        tool = {'action': 'tool', 'tool': 'items.create', 'arguments_json': '{}',
                'reply': '', 'document_title': '', 'document': ''}
        finish = {'action': 'finish', 'tool': '', 'arguments_json': '{}',
                  'reply': 'The item needs reconciliation.', 'document_title': '', 'document': ''}
        backend = Mock()
        backend.call.side_effect = [tool, finish]
        write = Mock(side_effect=KeyboardInterrupt())
        tools = ReadTools([ReadTool('items.create', 'Create item', object_schema({}), write, mutates=True)])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(KeyboardInterrupt):
                research(backend, tools, {'message': 'Create item'}, root)
            result = research(backend, tools, {'message': 'Create item'}, root)
            self.assertIn('unconfirmed', result['receipts'][0]['error'])
            self.assertEqual(write.call_count, 1)
            self.assertIn('reconcile', backend.call.call_args.args[1])

    def test_cancelled_checkpoint_does_not_resume(self):
        from capo.research_tools import research, ReadTools
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root/'cancelled.json', {'cancelled': True})
            backend = Mock()
            with self.assertRaises(RecoveryStopped):
                research(backend, ReadTools([]), {'message': 'Stop'}, root)
            backend.call.assert_not_called()


class ConversationRecoveryTests(unittest.TestCase):
    def test_slack_poll_resumes_deferred_request_without_an_error_reply(self):
        import time
        from unittest.mock import patch
        from capo.capabilities import CapabilityConversation
        from capo.conversation import ConversationPending
        finish = {'action': 'finish', 'tool': '', 'arguments_json': '{}',
                  'reply': 'The answer is ready.', 'document_title': '', 'document': ''}
        with tempfile.TemporaryDirectory() as tmp:
            router = CapabilityConversation(Path(tmp), {'recovery': {'base_delay': 1}})
            clock = [100]
            with patch('capo.capabilities.Providers') as factory, patch('capo.recovery.time.time', side_effect=lambda: clock[0]):
                factory.return_value.call.side_effect = [TimeoutError(), finish]
                context = {'message': 'Help with this request', 'aliases': [], 'objectives': []}
                with self.assertRaises(ConversationPending):
                    router.poll('original', context)
                for _ in range(500):
                    if list(router.root.glob('*/retry.json')):
                        break
                    time.sleep(.005)
                else:
                    self.fail('Retry was not scheduled')
                self.assertEqual(list(router.root.glob('*/outcome.json')), [])
                with self.assertRaises(ConversationPending):
                    router.poll('original', context)
                self.assertEqual(factory.return_value.call.call_count, 1)
                clock[0] = 200
                restarted = CapabilityConversation(Path(tmp), {'recovery': {'base_delay': 1}})
                for _ in range(500):
                    try:
                        result = restarted.poll('original', context)
                        break
                    except ConversationPending:
                        time.sleep(.005)
                else:
                    self.fail('Retry did not finish')
                self.assertEqual(result['reply'], finish['reply'])
                self.assertEqual(factory.return_value.call.call_count, 2)
