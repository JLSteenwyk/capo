import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock, patch

from capo.grok_quota import normalize, fetch, NoRedirect
from capo.subscription_probes import channel, claude_quota, grok_quota

PERIOD = {'type': 'USAGE_PERIOD_TYPE_WEEKLY', 'end': '2030-01-01T00:00:00Z'}


class SubscriptionTests(unittest.TestCase):
    def test_claude_protocol_uses_no_prompt_and_prefers_profile_capable_login(self):
        calls = []
        @contextmanager
        def fake_channel(argv, **kwargs):
            self.assertNotIn('CLAUDE_CODE_OAUTH_TOKEN', kwargs['env'])
            self.assertNotIn('ANTHROPIC_API_KEY', kwargs['env'])
            self.assertIn('--no-session-persistence', argv)
            self.assertEqual(argv[argv.index('--tools')+1], '')
            def request(payload, match):
                calls.append(payload['request']['subtype'])
                result = {'type': 'control_response', 'response': {'subtype': 'success',
                    'request_id': payload['request_id'], 'response': {'rate_limits_available': True,
                    'rate_limits': {'five_hour': {'utilization': 35, 'resets_at': PERIOD['end']},
                                    'seven_day': {'utilization': 80, 'resets_at': PERIOD['end']}},
                    'session': {'private': 'discard'}}}}
                self.assertTrue(match(result))
                return result
            yield request
        with patch.dict(os.environ, {'CLAUDE_CODE_OAUTH_TOKEN': 'synthetic', 'ANTHROPIC_API_KEY': 'synthetic'}), \
             patch('capo.subscription_probes.channel', fake_channel):
            rows = claude_quota()
            self.assertEqual(os.environ['CLAUDE_CODE_OAUTH_TOKEN'], 'synthetic')
        self.assertEqual(calls, ['initialize', 'get_usage'])
        self.assertEqual([r['used_percent'] for r in rows], [35, 80])
        self.assertNotIn('private', json.dumps(rows))

    def test_claude_missing_profile_or_malformed_limits_remain_unknown(self):
        values = [{'rate_limits_available': False}, {'rate_limits_available': True, 'rate_limits': {}},
                  {'rate_limits_available': True, 'rate_limits': {'five_hour': {'utilization': 5, 'resets_at': '2030-01-01'}}}]
        for value in values:
            with patch('capo.subscription_probes.claude_usage', return_value=value), self.assertRaises(ValueError):
                claude_quota()

    def test_grok_percent_and_proto_zero_require_recognized_window(self):
        self.assertEqual(normalize({'config': {'currentPeriod': PERIOD}})[0]['used_percent'], 0)
        for amount in (12.5, 100, 110):
            self.assertEqual(normalize({'config': {'currentPeriod': PERIOD, 'creditUsagePercent': amount}})[0]['used_percent'], amount)
        for config in ({}, {'creditUsagePercent': 0}, {'currentPeriod': {'type': 'new'}},
                       {'currentPeriod': PERIOD, 'creditUsagePercent': None},
                       {'currentPeriod': PERIOD, 'creditUsagePercent': float('nan')}):
            with self.assertRaises((ValueError, KeyError)):
                normalize({'config': config})

    def test_grok_fetch_is_fixed_get_without_redirect_or_credential_output(self):
        auth = {'account': {'oidc_issuer': 'https://auth.x.ai', 'key': 'synthetic-secret', 'user_id': 'synthetic-user'}}
        response = io.BytesIO(json.dumps({'config': {'currentPeriod': PERIOD, 'creditUsagePercent': 42},
                                        'private': 'discard'}).encode())
        opener = Mock()
        opener.open.return_value = response
        with patch.object(Path, 'open', return_value=io.StringIO(json.dumps(auth))), \
             patch('urllib.request.build_opener', return_value=opener) as build:
            rows = fetch()
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_method(), 'GET')
        self.assertEqual(request.full_url, 'https://cli-chat-proxy.grok.com/v1/billing?format=credits')
        self.assertEqual(request.get_header('Authorization'), 'Bearer synthetic-secret')
        self.assertIs(build.call_args.args[0], NoRedirect)
        self.assertIsNone(NoRedirect().redirect_request(None, None, None, None, None, None))
        self.assertNotIn('secret', json.dumps(rows))
        self.assertNotIn('private', json.dumps(rows))

    def test_grok_vm_keeps_credentials_inside_selected_worker(self):
        row = {'name': 'primary', 'used_percent': 25, 'reset_at': 1900000000}
        result = subprocess.CompletedProcess([], 0, json.dumps([row]).encode(), b'')
        with patch('capo.subscription_probes.subprocess.run', return_value=result) as run, \
             patch('capo.grok_quota.fetch') as local:
            self.assertEqual(grok_quota({'grok': {'transport': 'lima', 'vm': 'synthetic-vm'}}), [row])
        argv = run.call_args.args[0]
        self.assertEqual(argv[:7], ['limactl', 'shell', '--workdir=/tmp', 'synthetic-vm', 'timeout', '12s', 'python3'])
        self.assertEqual(run.call_args.kwargs['timeout'], 15)
        local.assert_not_called()

    def test_grok_vm_failure_never_reads_another_local_account(self):
        with patch('capo.subscription_probes.subprocess.run', return_value=subprocess.CompletedProcess([], 1, b'', b'private')), \
             patch('capo.grok_quota.fetch') as local, self.assertRaisesRegex(ValueError, 'worker quota unavailable'):
            grok_quota({'grok': {'transport': 'lima', 'vm': 'synthetic-vm'}})
        local.assert_not_called()

    def test_channel_cleans_up_timeout_and_oversized_reply(self):
        for script in ('import time; time.sleep(20)', 'import sys;sys.stdin.readline();print("a"*1000001,flush=True)'):
            children = []
            def popen(argv, **kwargs):
                p = subprocess.Popen([sys.executable, '-c', script], **kwargs)
                children.append(p)
                return p
            with self.assertRaises((TimeoutError, ValueError)):
                with channel(['synthetic'], timeout=0.3, popen=popen) as request:
                    request({'id': 1}, lambda v: True)
            self.assertIsNotNone(children[0].poll())

    def test_registry_probes_all_providers_with_correct_sources(self):
        from capo.capacity import Capacity
        row = {'name': 'primary', 'used_percent': 20, 'reset_at': 1900000000}
        with tempfile.TemporaryDirectory() as home:
            capacity = Capacity(home, clock=lambda: 1800000000,
                                probes={p: lambda: [row] for p in ('claude', 'codex', 'grok')})
            data = capacity.snapshot(refresh=True)
            self.assertEqual({p:v['source'] for p,v in data.items()},
                             {'claude':'claude_usage','codex':'codex_app_server','grok':'grok_billing'})
            self.assertTrue(all(v['remaining_percent'] == 80 for v in data.values()))
