"""Grok CLI billing read, also executable inside its configured worker VM.

Only normalized subscription windows leave this process; credentials never do.
"""
import json
import math
from datetime import datetime
from pathlib import Path
import urllib.request

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


def fetch():
    path = Path.home()/'.grok/auth.json'
    with path.open() as stream:
        raw = stream.read(1000001)
    if len(raw) > 1000000:
        raise ValueError('Grok authentication file too large')
    entries = json.loads(raw)
    eligible = [v for v in entries.values() if isinstance(v, dict)
                and v.get('oidc_issuer') == 'https://auth.x.ai' and v.get('key')]
    if len(eligible) != 1:
        raise ValueError('Grok login needs attention')
    auth = eligible[0]
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


if __name__ == '__main__':
    try:
        print(json.dumps(fetch()))
    except Exception:
        # Do not expose HTTP errors, headers or credential parser diagnostics.
        print('{"error":"grok_quota_unavailable"}')
        raise SystemExit(1)
