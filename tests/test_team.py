import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from capo.team import Specialist, roster
from capo.conversation import ConversationPending


class TeamTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)

    def finish(self, agent, key, message):
        for _ in range(400):
            try:return agent.poll(key, {'aliases': [], 'objectives': [], 'message': message})
            except ConversationPending:time.sleep(.005)
        self.fail('Specialist did not finish')

    def test_memory_scoped_by_role_owner_and_replay_cached(self):
        style = Specialist(self.home, 'style_assistant', 'owner-a')
        with patch('capo.team.Providers') as provider:
            provider.return_value.call.side_effect = [
                {'action':'tool','tool':'preferences.remember','arguments_json':json.dumps({'note':'Prefers blue.'}),
                 'reply':'','document_title':'','document':''},
                {'action':'finish','tool':'','arguments_json':'{}','reply':'I will use that preference.',
                 'document_title':'','document':''}]
            self.finish(style, 'first', 'I prefer blue.')
            self.finish(style, 'first', 'I prefer blue.')
            self.assertEqual(provider.return_value.call.call_count, 2)
            provider.return_value.call.side_effect = None
            provider.return_value.call.return_value = {'action':'finish','tool':'','arguments_json':'{}',
                'reply':'Here is some advice.','document_title':'','document':''}
            self.finish(Specialist(self.home, 'style_assistant', 'owner-a'), 'second', 'Suggest an outfit.')
            self.assertIn('Prefers blue.', provider.return_value.call.call_args.args[1])
            for role, owner in [('shopping_assistant', 'owner-a'), ('style_assistant', 'owner-b')]:
                self.finish(Specialist(self.home, role, owner), 'other', 'Help me.')
                self.assertNotIn('Prefers blue.', provider.return_value.call.call_args.args[1])
            self.assertEqual((style.root/'memory.sqlite3').stat().st_mode & 0o777, 0o600)

    def test_roster_does_not_claim_missing_connections(self):
        team = roster({'repositories': {'example': {}}, 'calendar': {'enabled': True}})
        self.assertTrue(team['connections']['calendar'])
        self.assertFalse(team['connections']['email'])
        self.assertFalse(team['connections']['retail_accounts'])
        self.assertEqual(len(team['specialists']), 3)

    def test_unknown_role_rejected(self):
        with self.assertRaises(ValueError):Specialist(self.home, '../other', 'owner')
