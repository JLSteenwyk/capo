import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from capo.grok_quota import fetch, GrokLoginRequired, renew
from capo.subscription_auth import LoginRequired
from capo.subscription_probes import claude_quota, grok_quota
from capo.capacity import Capacity
from capo.quota_probe import codex_quota

WINDOW = [{'name': 'primary', 'used_percent': 12, 'reset_at': 1900000000}]
AUTH = {'key': 'old-synthetic', 'refresh_token': 'synthetic-refresh', 'user_id': 'same', 'oidc_issuer': 'https://auth.x.ai'}


def denied(code=401):
    return HTTPError('https://synthetic.invalid', code, 'private diagnostic', {}, io.BytesIO())


class RecoveryTests(unittest.TestCase):
    def test_grok_renews_once_and_rereads_rotated_credentials(self):
        new = dict(AUTH, key='new-synthetic')
        with patch('capo.grok_quota.credentials', side_effect=[AUTH, new]), patch('capo.grok_quota.billing', side_effect=[denied(), WINDOW]) as billing, patch('capo.grok_quota.renew') as refresh:
            self.assertEqual(fetch('/synthetic/grok'), WINDOW)
        refresh.assert_called_once_with('/synthetic/grok')
        self.assertEqual(billing.call_args.args[0], new)

    def test_grok_permanent_denial_stops_after_one_refresh(self):
        with patch('capo.grok_quota.credentials', return_value=AUTH), patch('capo.grok_quota.billing', side_effect=denied()) as billing, patch('capo.grok_quota.renew') as refresh:
            with self.assertRaises(GrokLoginRequired):fetch()
        self.assertEqual(billing.call_count, 2)
        refresh.assert_called_once()

    def test_grok_does_not_refresh_network_or_billing_errors(self):
        for code in (403, 429, 500):
            with self.subTest(code=code), patch('capo.grok_quota.credentials', return_value=AUTH), patch('capo.grok_quota.billing', side_effect=denied(code)), patch('capo.grok_quota.renew') as refresh:
                with self.assertRaises(HTTPError):fetch()
                refresh.assert_not_called()

    def test_grok_never_switches_accounts_or_runs_login_without_refresh_token(self):
        for creds in ([AUTH, dict(AUTH, user_id='different')], [dict(AUTH, refresh_token=None)]):
            with patch('capo.grok_quota.credentials', side_effect=creds), patch('capo.grok_quota.billing', side_effect=denied()) as billing, patch('capo.grok_quota.renew') as refresh:
                with self.assertRaises(GrokLoginRequired):fetch()
                self.assertEqual(billing.call_count, 1)
                self.assertEqual(refresh.call_count, len(creds)-1)

    def test_grok_native_refresh_has_no_prompt_or_output_and_kills_child(self):
        process = Mock(pid=12345)
        process.wait.side_effect = [subprocess.TimeoutExpired('grok', 25), 0]
        with patch('capo.grok_quota.subprocess.Popen', return_value=process) as popen, patch('capo.grok_quota.os.killpg') as kill, patch.dict(os.environ, {'XAI_API_KEY': 'synthetic'}):
            with self.assertRaises(subprocess.TimeoutExpired):renew('/synthetic/grok')
        self.assertEqual(popen.call_args.args[0], ['/synthetic/grok', 'models'])
        self.assertEqual(popen.call_args.kwargs['stdin'], subprocess.DEVNULL)
        self.assertNotIn('XAI_API_KEY', popen.call_args.kwargs['env'])
        kill.assert_called_once()

    def test_guest_auth_error_is_redacted_and_never_falls_back_to_host(self):
        result = subprocess.CompletedProcess([], 1, b'{"error":"login_required"}\n', b'synthetic-private')
        with patch('capo.subscription_probes.subprocess.run', return_value=result), patch('capo.grok_quota.fetch') as host:
            with self.assertRaises(LoginRequired):grok_quota({'grok': {'transport':'lima', 'vm':'synthetic'}})
        host.assert_not_called()

    def test_claude_native_refresh_recovery_is_bounded(self):
        ok = {'rate_limits_available':True, 'rate_limits':{'five_hour':{'utilization':12,'resets_at':'2030-01-01T00:00:00Z'}}}
        with patch('capo.subscription_probes.claude_usage', side_effect=[LoginRequired(), ok]) as read:
            self.assertEqual(claude_quota()[0]['used_percent'], 12)
            self.assertEqual(read.call_count, 2)
        with patch('capo.subscription_probes.claude_usage', side_effect=LoginRequired()) as read:
            with self.assertRaises(LoginRequired):claude_quota()
            self.assertEqual(read.call_count, 2)
        with patch('capo.subscription_probes.claude_usage', side_effect=TimeoutError()) as read:
            with self.assertRaises(TimeoutError):claude_quota()
            self.assertEqual(read.call_count, 1)

    def test_claude_null_usage_gets_one_fresh_cli_attempt(self):
        with patch('capo.subscription_probes.claude_usage', return_value={'rate_limits_available':True,'rate_limits':None}) as read:
            with self.assertRaises(ValueError):claude_quota()
            self.assertEqual(read.call_count, 2)

    def test_auth_backoff_and_recovery_are_shared_without_diagnostics(self):
        now = [1800000000]
        probe = Mock(side_effect=[LoginRequired(), WINDOW])
        with tempfile.TemporaryDirectory() as home:
            c = Capacity(home, clock=lambda:now[0], probes={'grok':probe})
            first = c.snapshot(['grok'], refresh=True)['grok']
            self.assertEqual(first['probe_error'], 'login_required')
            self.assertTrue(first['action_required'])
            self.assertEqual(first['next_check_at'], now[0]+300)
            now[0]+=61
            c.snapshot(['grok'], refresh=True)
            self.assertEqual(probe.call_count, 1)
            now[0]+=240
            recovered = c.snapshot(['grok'], refresh=True)['grok']
            self.assertIsNone(recovered['probe_error'])
            self.assertIsNone(recovered['action_required'])
            self.assertEqual(recovered['remaining_percent'], 88)

    def test_codex_auth_error_refreshes_once_through_account_rpc(self):
        for recover in (True, False):
            script = '''import json,sys
for expected in ['initialize','initialized','account/read','account/rateLimits/read','account/read','account/rateLimits/read']:
 v=json.loads(sys.stdin.readline()); assert v['method']==expected
 if expected=='initialized':continue
 if expected=='account/read':
  assert v['params']['refreshToken']==(v['id']==4)
  result={'account':{'type':'chatgpt'}}
 elif expected=='account/rateLimits/read':
  if v['id']==3 or not RECOVER:
   print(json.dumps({'id':v['id'],'error':{'message':'401 unauthorized synthetic-secret'}}),flush=True);continue
  result={'rateLimits':{'primary':{'usedPercent':12,'resetsAt':1900000000}}}
 else:result={}
 print(json.dumps({'id':v['id'],'result':result}),flush=True)
sys.stdin.read()
'''.replace('RECOVER', repr(recover))
            children=[]
            def popen(argv, **kwargs):
                p=subprocess.Popen([sys.executable,'-c',script],**kwargs);children.append(p);return p
            if recover:self.assertEqual(codex_quota(popen=popen),WINDOW)
            else:
                with self.assertRaises(LoginRequired) as error:codex_quota(popen=popen)
                self.assertNotIn('synthetic-secret',str(error.exception))
            self.assertIsNotNone(children[0].poll())
