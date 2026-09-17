import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from capo.capacity import Capacity, claude_windows, codex_windows
from capo.contracts import TEXT, object_schema
from capo.providers import Providers
from capo.recovery import RateLimited, RecoveringProvider, RetryLater


def window(used, reset=1000, name='primary'):
    return {'name': name, 'used_percent': used, 'reset_at': reset}


class CapacityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = 100
        self.capacity = Capacity(self.root, clock=lambda: self.now, probes={})

    def test_two_windows_use_tightest_limit_and_last_blocking_reset(self):
        self.capacity.observe('codex', [window(100, 150), window(100, 900, 'secondary')], 'codex_app_server')
        self.assertEqual(self.capacity.snapshot()['codex']['retry_at'], 900)
        self.now = 200
        with self.assertRaises(RateLimited) as error:
            self.capacity.choose('codex', ['codex'])
        self.assertEqual(error.exception.reset_at, 900)
        self.now = 901
        self.assertEqual(self.capacity.snapshot()['codex']['state'], 'unknown')

    def test_stale_positive_is_unknown_but_exhaustion_survives_until_reset(self):
        self.capacity.observe('codex', [window(20, 1000)], 'codex_app_server')
        self.capacity.observe('grok', [window(100, 1000)], 'codex_app_server')
        self.now = 401
        rows = self.capacity.snapshot()
        self.assertEqual(rows['codex']['state'], 'unknown')
        self.assertIsNone(rows['codex']['remaining_percent'])
        self.assertEqual(rows['grok']['state'], 'exhausted')

    def test_missing_windows_and_shorter_cooldowns_cannot_erase_known_exhaustion(self):
        self.capacity.observe('codex', [window(100, 900)], 'codex_app_server')
        self.now += 1
        self.capacity.observe('codex', [], 'codex_app_server')
        self.assertEqual(self.capacity.snapshot()['codex']['retry_at'], 900)
        self.capacity.limited('grok', 800)
        self.capacity.limited('grok', 200)
        self.assertEqual(self.capacity.snapshot()['grok']['retry_at'], 800)
        # A newer actual measurement of that same window supersedes it.
        self.capacity.observe('codex', [window(10, 900)], 'codex_app_server')
        self.assertEqual(self.capacity.snapshot()['codex']['state'], 'available')

    def test_routing_preferences_pins_and_unknown(self):
        self.assertEqual(self.capacity.choose('grok', ['codex', 'grok'])[0], 'grok')
        self.capacity.observe('codex', [window(10)], 'codex_app_server')
        self.assertEqual(self.capacity.choose('grok', ['codex', 'grok'])[0], 'codex')
        self.assertEqual(self.capacity.choose('grok', ['grok'])[0], 'grok')
        self.capacity.limited('codex', 700)
        self.assertEqual(self.capacity.choose('codex', ['codex', 'grok'])[0], 'grok')
        self.capacity.limited('grok', 800)
        with self.assertRaises(RateLimited) as error:
            self.capacity.choose('codex', ['codex', 'grok'])
        self.assertEqual(error.exception.reset_at, 700)
        with self.assertRaises(ValueError):
            self.capacity.choose('claude', ['grok'])

    def test_refresh_cache_failure_redaction_and_recovery(self):
        probe = Mock(return_value=[window(10)])
        self.capacity.probes = {'codex': probe}
        self.capacity.snapshot(refresh=True)
        self.capacity.snapshot(refresh=True)
        self.assertEqual(probe.call_count, 1)
        self.now += 61
        probe.side_effect = ValueError('private secret diagnostic')
        self.assertEqual(self.capacity.snapshot(refresh=True)['codex']['remaining_percent'], 90)
        self.now += 301
        self.assertEqual(self.capacity.snapshot(refresh=True)['codex']['state'], 'unknown')
        self.assertNotIn('secret', (self.capacity.root/'observations.json').read_text())
        self.assertEqual((self.capacity.root/'observations.json').stat().st_mode & 0o777, 0o600)
        probe.side_effect = None
        self.now += 61
        self.assertEqual(self.capacity.snapshot(refresh=True)['codex']['state'], 'available')

    def test_concurrent_refresh_does_not_duplicate_probe_or_lose_cooldown(self):
        entered, finish = threading.Event(), threading.Event()
        def probe():
            entered.set()
            finish.wait(3)
            return [window(0)]
        self.capacity.probes = {'codex': Mock(side_effect=probe)}
        thread = threading.Thread(target=lambda: self.capacity.snapshot(refresh=True))
        thread.start()
        try:
            self.assertTrue(entered.wait(1))
            self.capacity.snapshot(refresh=True)
            other = Capacity(self.root, clock=lambda: self.now, probes={})
            other.limited('codex', 950)
        finally:
            finish.set()
            thread.join(3)
        self.assertEqual(self.capacity.probes['codex'].call_count, 1)
        self.assertEqual(self.capacity.snapshot()['codex']['retry_at'], 950)

    def test_provider_reports_global_cooldown_and_preflight_does_not_invoke(self):
        provider = Providers(config={}, capacity=self.capacity)
        with patch.object(provider, '_call', side_effect=RateLimited(800)) as invoke:
            with self.assertRaises(RateLimited):
                provider.call('grok', 'synthetic', {}, self.root, self.root/'one')
            other = Providers(config={}, capacity=Capacity(self.root, clock=lambda: self.now, probes={}))
            with patch.object(other, '_call') as duplicate:
                with self.assertRaises(RateLimited):
                    other.call('grok', 'another task', {}, self.root, self.root/'two')
                duplicate.assert_not_called()
            self.assertEqual(invoke.call_count, 1)

    def test_recovery_wait_does_not_spend_attempts_and_receipt_is_stable(self):
        self.capacity.limited('claude', 500)
        backend = Mock(capacity=self.capacity)
        backend.call.return_value = {'answer': 'done'}
        directory = self.root/'op'
        def run(config=None, images=None):
            return RecoveringProvider(backend, config, clock=lambda: self.now).call(
                'claude', 'synthetic', object_schema({'answer': TEXT}), self.root, directory, images=images)
        with self.assertRaises(RetryLater):
            run()
        self.assertEqual(json.loads((directory/'recovery.json').read_text())['attempts'], [])
        backend.call.assert_not_called()
        self.now = 501
        self.assertEqual(run(), {'answer': 'done'})
        self.capacity.limited('claude', 900)
        self.assertEqual(run(), {'answer': 'done'})
        self.assertEqual(backend.call.call_count, 1)

    def test_explicit_fallback_and_images_keep_capability_boundaries(self):
        self.capacity.limited('claude', 500)
        backend = Mock(capacity=self.capacity)
        backend.call.return_value = {'answer': 'done'}
        schema = object_schema({'answer': TEXT})
        config = {'fallbacks': {'claude': ['grok']}}
        wrapper = RecoveringProvider(backend, config, clock=lambda: self.now)
        wrapper.call('claude', 'synthetic', schema, self.root, self.root/'text')
        self.assertEqual(backend.call.call_args.args[0], 'grok')
        with self.assertRaises(RetryLater):
            wrapper.call('claude', 'synthetic', schema, self.root, self.root/'image', images=[{'data': 'synthetic'}])
        self.assertEqual(backend.call.call_count, 1)

    def test_normalizers_only_keep_actual_quota_and_target_bucket(self):
        row = codex_windows({'rateLimits': {'limitId': 'other', 'primary': {'usedPercent': 20, 'resetsAt': 300}}})
        self.assertEqual(row, [])
        row = codex_windows({'email': 'private', 'rateLimitsByLimitId': {'codex': {
            'primary': {'usedPercent': 40, 'resetsAt': 300}}}})
        self.assertEqual(row, [window(40, 300)])
        self.assertEqual(claude_windows({'context_window': {'used_percentage': 80}}), [])
        self.assertEqual(claude_windows({'session_id': 'private', 'rate_limits': {
            'five_hour': {'used_percentage': 10, 'resets_at': 500}}}), [window(10, 500, 'five_hour')])
        for used in (True, -1, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                self.capacity.observe('codex', [window(used)], 'codex_app_server')

    def test_shared_tool_and_cli_are_read_only_and_do_not_expose_raw_payload(self):
        from capo.capabilities import Documents, shared_tools
        from capo.cli import main
        with patch('capo.quota_probe.codex_quota', return_value=[window(25)]), \
             patch('capo.subscription_probes.claude_quota', return_value=[]), \
             patch('capo.subscription_probes.grok_quota', return_value=[]):
            registry = shared_tools(self.root, {}, Documents(self.root, 'synthetic'))
            tool = registry.tools['workers.capacity']
            self.assertFalse(tool.mutates)
            self.assertEqual(tool.execute()['codex']['source'], 'codex_app_server')
        payload = {'session_id': 'private session', 'rate_limits': {'five_hour': {'used_percentage': 10, 'resets_at': 500}}}
        with patch('sys.stdin', io.StringIO(json.dumps(payload))), patch('sys.stdout', new_callable=io.StringIO) as out:
            self.assertEqual(main(['--home', str(self.root), 'capacity-claude']), 0)
            self.assertEqual(out.getvalue(), '')
        self.assertNotIn('private session', (self.capacity.root/'observations.json').read_text())

    def test_cli_home_override_reaches_nested_providers_and_is_restored(self):
        from capo.cli import main
        def doctor(_):
            self.assertEqual(Providers(config={}).capacity.root, self.root.resolve()/'capacity')
            return 0
        with patch.dict(os.environ, {'CAPO_HOME': '/synthetic/original'}), patch('capo.cli.doctor', side_effect=doctor):
            self.assertEqual(main(['--home', str(self.root), 'doctor']), 0)
            self.assertEqual(os.environ['CAPO_HOME'], '/synthetic/original')


class QuotaProbeTests(unittest.TestCase):
    def test_protocol_uses_only_initialize_and_quota_read(self):
        from capo.quota_probe import codex_quota
        script = '''import json, sys
v=json.loads(sys.stdin.readline()); assert v['method']=='initialize'
print(json.dumps({'id':v['id'],'result':{}}),flush=True)
v=json.loads(sys.stdin.readline()); assert v['method']=='initialized'
v=json.loads(sys.stdin.readline()); assert v['method']=='account/rateLimits/read'
print(json.dumps({'id':v['id'],'result':{'rateLimits':{'primary':{'usedPercent':12,'resetsAt':1000}}}}),flush=True)
sys.stdin.read()
'''
        children = []
        def popen(argv, **kwargs):
            self.assertEqual(argv, ['codex', 'app-server', '--listen', 'stdio://'])
            p = subprocess.Popen([sys.executable, '-c', script], **kwargs)
            children.append(p)
            return p
        self.assertEqual(codex_quota(popen=popen), [window(12)])
        self.assertIsNotNone(children[0].poll())

    def test_timeout_and_malformed_response_clean_up_child(self):
        from capo.quota_probe import codex_quota
        for script in ('import time; time.sleep(30)', 'print("bad json", flush=True)'):
            children = []
            def popen(argv, **kwargs):
                p = subprocess.Popen([sys.executable, '-c', script], **kwargs)
                children.append(p)
                return p
            with self.assertRaises((TimeoutError, ValueError)):
                codex_quota(timeout=0.1, popen=popen)
            self.assertIsNotNone(children[0].poll())
