import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from capo.conversation import ConversationRouter, _write
from capo.request_control import Requests, STOPPED
from capo.recovery import RecoveryStopped
from capo.research_tools import ReadTools, ReadTool, research
from capo.contracts import object_schema


class RequestControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)

    def started(self, owner, thread, event='request'):
        root = self.home/'capabilities'/hashlib.sha256(owner.encode()).hexdigest()
        router = ConversationRouter(root)
        directory = router.root/hashlib.sha256(event.encode()).hexdigest()
        directory.mkdir()
        _write(directory/'started.json', {'context':{'request_thread':thread}, 'schema':{}})
        _write(directory/'retry.json', {'retry_at':99999999999})
        return router, directory

    def test_cancellation_is_owner_and_thread_scoped_and_bypasses_retry_wait(self):
        router, directory = self.started('owner', 'thread')
        _, other_thread = self.started('owner', 'elsewhere', 'second')
        _, other_owner = self.started('other', 'thread')
        self.assertEqual(Requests(self.home, 'owner', 'thread').cancel('cancel-event'), 1)
        self.assertEqual(router.poll('request', {})['reply'], STOPPED)
        self.assertFalse((other_thread/'cancelled.json').exists())
        self.assertFalse((other_owner/'cancelled.json').exists())
        self.assertEqual(Requests(self.home, 'owner', 'thread').cancel('again'), 0)

    def test_completed_request_is_not_cancelled(self):
        _, directory = self.started('owner', 'thread')
        _write(directory/'outcome.json', {'complete':True})
        self.assertEqual(Requests(self.home, 'owner', 'thread').cancel('cancel'), 0)

    def test_cancel_during_reasoning_prevents_selected_write(self):
        directory = self.home/'research'
        def decide(*args):
            _write(directory/'cancelled.json', {'cancelled':True})
            return {'action':'tool','tool':'items.save','arguments_json':'{}','reply':'','document_title':'','document':''}
        provider = Mock()
        provider.call.side_effect = decide
        write = Mock()
        tools = ReadTools([ReadTool('items.save', 'Save item', object_schema({}), write, True)])
        with self.assertRaises(RecoveryStopped):
            research(provider, tools, {}, directory)
        write.assert_not_called()

    def test_cancel_during_write_preserves_receipt_and_stops_future_steps(self):
        import json
        directory = self.home/'research'
        def write(**kwargs):
            _write(directory/'cancelled.json', {'cancelled':True})
            return {'saved':True}
        provider = Mock()
        provider.call.return_value = {'action':'tool','tool':'items.save','arguments_json':'{}','reply':'','document_title':'','document':''}
        tools = ReadTools([ReadTool('items.save','Save item',object_schema({}),write,True)])
        with self.assertRaises(RecoveryStopped):
            research(provider,tools,{},directory)
        receipts = json.loads((directory/'receipts.json').read_text())
        self.assertEqual(receipts[0]['result'], {'saved':True})
        self.assertEqual(provider.call.call_count, 1)
