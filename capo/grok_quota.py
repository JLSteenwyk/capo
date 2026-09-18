"""Grok CLI billing read, also executable inside its configured worker VM.

Only normalized subscription windows leave this process; credentials never do.
"""
import json
import math
from datetime import datetime
from pathlib import Path
import urllib.request
import urllib.error
import subprocess
import tempfile
import os
import signal

URL = 'https://cli-chat-proxy.grok.com/v1/billing?format=credits'


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def normalize(value):
    config = value.get('config')
    if not isinstance(config, dict):
        raise ValueError('Grok quota response unavailable')
    period = config.get('currentPeriod') or {}
    if period.get('type') not in ('USAGE_PERIOD_TYPE_WEEKLY', 'USAGE_PERIOD_TYPE_MONTHLY'):
        raise ValueError('Grok quota window unavailable')
    reset = datetime.fromisoformat(period['end'].replace('Z', '+00:00'))
    if reset.tzinfo is None:
        raise ValueError('Grok reset needs a timezone')
    # This CLI endpoint uses protobuf JSON: scalar zero is omitted. Require
    # the recognized subscription-period envelope before applying that default.
    used = config.get('creditUsagePercent', 0)
    if type(used) not in (float, int) or not math.isfinite(used) or used < 0:
        raise ValueError('Invalid Grok quota percentage')
    return [{'name': 'primary', 'used_percent': used, 'reset_at': reset.timestamp()}]


class GrokLoginRequired(ValueError):
    pass


def credentials():
    path = Path.home()/'.grok/auth.json'
    try:
        stream = path.open()
    except FileNotFoundError:
        raise GrokLoginRequired('Grok login required') from None
    with stream:
        raw = stream.read(1000001)
    if len(raw) > 1000000:
        raise ValueError('Grok authentication file too large')
    entries = json.loads(raw)
    eligible = [v for v in entries.values() if isinstance(v, dict)
                and v.get('oidc_issuer') == 'https://auth.x.ai' and v.get('key')]
    if len(eligible) != 1:
        raise GrokLoginRequired('Grok login required')
    return eligible[0]


def billing(auth):
    headers = {'Authorization': 'Bearer '+auth['key'], 'Accept': 'application/json',
               'x-xai-token-auth': 'xai-grok-cli'}
    if auth.get('user_id'):
        headers['x-userid'] = auth['user_id']
    request = urllib.request.Request(URL, headers=headers)
    with urllib.request.build_opener(NoRedirect).open(request, timeout=8) as response:
        payload = response.read(1000001)
    if len(payload) > 1000000:
        raise ValueError('Grok quota response too large')
    return normalize(json.loads(payload))


def renew(binary):
    # A metadata-only native CLI command loads the OAuth refresh manager. The
    # CLI owns refresh-token rotation and its credential-file locking. Never
    # launch interactive login, submit a prompt, or copy tokens to the host.
    env = os.environ.copy()
    env.pop('XAI_API_KEY', None)
    env['GROK_DISABLE_AUTOUPDATER'] = '1'
    with tempfile.TemporaryDirectory(prefix='capo-grok-auth-') as cwd:
        process = subprocess.Popen([str(Path(binary).expanduser()), 'models'],
            cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True)
        try:
            process.wait(timeout=25)
        finally:
            try:os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:pass
            try:process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=1)
    # Some CLI builds exit badly after persisting renewed credentials. Only
    # the subsequent authenticated billing response proves recovery succeeded.


def fetch(binary='~/.grok/bin/grok'):
    auth = credentials()
    try:
        return billing(auth)
    except urllib.error.HTTPError as exc:
        exc.close()
        if exc.code != 401:raise
    if not auth.get('refresh_token'):raise GrokLoginRequired('Grok login required')
    renew(binary)
    renewed = credentials()
    if (renewed.get('user_id'), renewed.get('oidc_issuer')) != (auth.get('user_id'), auth.get('oidc_issuer')):
        raise GrokLoginRequired('Grok account changed during refresh')
    try:
        return billing(renewed)
    except urllib.error.HTTPError as exc:
        exc.close()
        if exc.code == 401:raise GrokLoginRequired('Grok login required') from None
        raise


if __name__ == '__main__':
    try:
        print(json.dumps(fetch(globals().get('GROK_BINARY', '~/.grok/bin/grok'))))
    except GrokLoginRequired:
        print('{"error":"login_required"}')
        raise SystemExit(1)
    except Exception:
        # Do not expose HTTP errors, headers or credential parser diagnostics.
        print('{"error":"grok_quota_unavailable"}')
        raise SystemExit(1)
