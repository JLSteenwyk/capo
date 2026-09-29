import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from capo.capabilities import Documents, owner_key, shared_tools
from capo.conversation import ConversationPending
from capo.slack import SlackService
from capo.slack_status_reactions import AcknowledgeTool
from capo.store import Store

CONFIG = {'team_id': 'T1', 'channel_id': 'C1', 'owner_user_id': 'UOWNER', 'auto_run': False, 'repositories': {}}


def body(event_id='owner-event', ts='100.1', user='UOWNER', text='Please check this.'):
    return {'team_id': 'T1', 'event_id': event_id,
            'event': {'type': 'message', 'channel': 'C1', 'user': user, 'ts': ts, 'text': text}}


class StatusReactionTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)/'state'
        repo = Path(tmp.name)/'repo'; repo.mkdir()
        from capo.repository import git
        git(repo, 'init', '-b', 'main')
        CONFIG['repositories'] = {'project': {'path': str(repo), 'checks': ['python3 -c "print(1)"'],
                                              'workers': ['codex'], 'reviewer': 'claude'}}
        self.store = Store(self.home); self.addCleanup(self.store.db.close)
        self.client = Mock()
        self.service = SlackService(self.store, CONFIG, self.client)
        self.sent = []
        patcher = patch.object(SlackService, 'send_chunk', side_effect=lambda event, text: self.sent.append(text) or True)
        patcher.start(); self.addCleanup(patcher.stop)

    def reactions(self):
        return [(call[0], call.kwargs['name']) for call in self.client.method_calls
                if call[0] in ('reactions_add', 'reactions_remove')]

    def run_message(self, dispatch, event=None):
        self.store.enqueue_slack((event or body())['event_id'], event or body())
        with patch.object(SlackService, 'dispatch', side_effect=dispatch):
            self.service.process_messages()

    def outcome(self, event_id, status):
        directory = (self.home / 'capabilities' / hashlib.sha256(owner_key(CONFIG).encode()).hexdigest()
                     / 'conversation' / hashlib.sha256(event_id.encode()).hexdigest())
        directory.mkdir(parents=True)
        (directory / 'outcome.json').write_text(json.dumps({'route': {}, 'status': status}))

    def test_eyes_while_working_then_check_after_the_reply(self):
        self.run_message(lambda *a, **k: 'Here is the answer.')
        self.assertEqual(self.sent, ['Here is the answer.'])
        self.assertEqual(self.reactions(), [('reactions_add', 'eyes'), ('reactions_remove', 'eyes'),
                                            ('reactions_add', 'white_check_mark')])
        self.assertTrue(all(c.kwargs['timestamp'] == '100.1' and c.kwargs['channel'] == 'C1'
                            for c in self.client.method_calls if c[0].startswith('reactions_')))

    def test_pending_work_reacts_once_and_failures_get_a_warning(self):
        self.store.enqueue_slack('owner-event', body())
        with patch.object(SlackService, 'dispatch', side_effect=ConversationPending()):
            self.service.process_messages(); self.service.process_messages()
        self.assertEqual(self.reactions(), [('reactions_add', 'eyes')])
        with patch.object(SlackService, 'dispatch', side_effect=ValueError('broken')):
            self.service.process_messages()
        self.assertEqual(self.reactions()[-1], ('reactions_add', 'warning'))
        self.assertIn('Could not handle this request', self.sent[0])

    def test_partial_outcome_is_marked_as_not_finished(self):
        self.outcome('owner-event', 'partial')
        self.run_message(lambda *a, **k: 'I could only check part of it.')
        self.assertEqual(self.reactions()[-1], ('reactions_add', 'warning'))

    def test_acknowledgment_reacts_thumbs_up_instead_of_replying(self):
        request = {'owner_request': {'event': 'owner-event'}}
        def dispatch(*args, **kwargs):
            tools = shared_tools(self.home, CONFIG, Documents(self.home, owner_key(CONFIG)), request)
            self.assertTrue(tools.call('slack.acknowledge', {}, operation_id='ack')['acknowledged'])
            return 'You’re welcome!'
        self.run_message(dispatch, body(text='thanks!'))
        self.assertEqual(self.sent, [])
        self.assertEqual(self.reactions()[-1], ('reactions_add', '+1'))
        self.assertEqual(self.store.pending_slack(), [])

    def test_reaction_errors_never_block_the_reply(self):
        self.client.reactions_add.side_effect = RuntimeError('missing_scope')
        self.client.reactions_remove.side_effect = RuntimeError('no_reaction')
        self.run_message(lambda *a, **k: 'Delivered anyway.')
        self.assertEqual(self.sent, ['Delivered anyway.'])
        self.assertEqual(self.store.pending_slack(), [])

    def test_only_the_authenticated_owner_message_can_be_acknowledged(self):
        self.store.enqueue_slack('other', body('other', user='UOTHER'))
        with self.assertRaises(PermissionError):
            AcknowledgeTool(self.home, CONFIG, {'owner_request': {'event': 'other'}}).acknowledge('op')
        with self.assertRaises(PermissionError):
            AcknowledgeTool(self.home, CONFIG, {'owner_request': {'event': 'missing'}}).acknowledge('op')
        self.assertNotIn('slack.acknowledge', shared_tools(self.home, CONFIG, Documents(self.home, owner_key(CONFIG))).tools)


if __name__ == '__main__':
    unittest.main()
