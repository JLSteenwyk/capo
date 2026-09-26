import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from capo.slack_connection import ConnectionWatchdog


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.now=0
        self.handlers=[]
        def factory():
            handler=Mock();handler.client.is_connected.return_value=False
            self.handlers.append(handler)
            return handler
        self.factory=Mock(side_effect=factory)
        self.watch=ConnectionWatchdog(self.factory,self.tmp.name,clock=lambda:self.now)

    def test_transient_sdk_recovery_does_not_replace_client(self):
        self.watch.step();first=self.handlers[0]
        self.now=10;self.watch.step()
        first.client.is_connected.return_value=True
        self.now=20;self.watch.step()
        self.assertTrue(self.watch.connected())
        self.assertEqual(self.factory.call_count,1)
        first.close.assert_not_called()

    def test_persistent_disconnect_replaces_and_preserves_callback_factory(self):
        self.watch.step();first=self.handlers[0]
        self.now=31;self.watch.step()
        first.close.assert_called_once()
        self.assertEqual(self.factory.call_count,2)
        self.handlers[1].client.is_connected.return_value=True
        self.now=32;self.watch.step()
        state=json.loads((Path(self.tmp.name)/'slack-connection.json').read_text())
        self.assertEqual(state['status'],'connected')
        self.assertEqual((Path(self.tmp.name)/'slack-connection.json').stat().st_mode & 0o777,0o600)

    def test_failed_login_backoff_caps_and_stable_connection_resets(self):
        self.factory.side_effect=RuntimeError('secret must not be recorded')
        for at in (0,30,90,210,450,750):
            self.now=at
            with self.assertRaises(RuntimeError):self.watch.step()
        self.assertEqual(self.watch.next_attempt,1050)
        self.now=800;self.watch.step();self.assertEqual(self.factory.call_count,6)
        self.assertNotIn('secret',(Path(self.tmp.name)/'slack-connection.json').read_text())
        self.watch.handler=Mock();self.watch.handler.client.is_connected.return_value=True
        self.watch.step();self.now=861;self.watch.step()
        self.assertEqual(self.watch.failures,0)

    def test_failed_close_never_creates_competing_client(self):
        self.watch.step();self.handlers[0].close.side_effect=RuntimeError()
        self.now=31
        with self.assertRaises(RuntimeError):self.watch.step()
        self.assertEqual(self.factory.call_count,1)
        self.now=32;self.watch.step();self.assertEqual(self.factory.call_count,1)

    def test_stop_prevents_reconnection(self):
        self.watch.close();self.watch.step();self.factory.assert_not_called()

    def test_background_loop_closes_on_shutdown_and_sanitizes_errors(self):
        def fail():
            self.watch.stopped.set()
            raise RuntimeError('private provider payload')
        self.watch.step=fail
        self.watch.run()
        state=json.loads((Path(self.tmp.name)/'slack-connection.json').read_text())
        self.assertEqual(state['status'],'reconnect_failed')
        self.assertNotIn('private',json.dumps(state))
