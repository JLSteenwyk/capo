"""Read-only CLI control requests; no prompts, sessions, tools or credit changes."""
import json
import os
import selectors
import signal
import subprocess
import tempfile
import time
from contextlib import contextmanager


@contextmanager
def channel(argv, timeout=15, popen=subprocess.Popen, env=None):
    with tempfile.TemporaryDirectory(prefix='capo-subscription-') as cwd:
        process = popen(argv, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL, start_new_session=True, env=env)
        selector = selectors.DefaultSelector()
        deadline, pending, total = time.monotonic()+timeout, b'', 0
        try:
            selector.register(process.stdout, selectors.EVENT_READ)
            def request(payload, match):
                nonlocal pending, total
                process.stdin.write((json.dumps(payload)+'\n').encode())
                process.stdin.flush()
                while True:
                    while b'\n' in pending:
                        line, pending = pending.split(b'\n', 1)
                        value = json.loads(line)
                        if match(value):
                            return value
                    remaining = deadline-time.monotonic()
                    if remaining <= 0 or not selector.select(remaining):
                        raise TimeoutError('Subscription probe timed out')
                    chunk = os.read(process.stdout.fileno(), 65536)
                    if not chunk:
                        raise ValueError('Subscription probe ended early')
                    total += len(chunk)
                    if total > 1000000:
                        raise ValueError('Subscription response too large')
                    pending += chunk
            yield request
        finally:
            selector.close()
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=1)
            process.stdin.close()
            process.stdout.close()


def claude_usage():
    argv = ['claude', '-p', '--input-format', 'stream-json', '--output-format', 'stream-json',
            '--verbose', '--tools', '', '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}',
            '--setting-sources', '', '--permission-mode', 'dontAsk', '--no-session-persistence',
            '--settings', '{"autoMemoryEnabled":false,"disableAllHooks":true}']
    env = os.environ.copy()
    # Setup tokens support inference but lack user:profile. Use the owner's
    # normal CLI login for usage; leave task execution authentication unchanged.
    env.pop('CLAUDE_CODE_OAUTH_TOKEN', None)
    env.pop('ANTHROPIC_API_KEY', None)
    with channel(argv, env=env) as request:
        request({'type': 'control_request', 'request_id': 'init',
                 'request': {'subtype': 'initialize'}},
                lambda v: v.get('type') == 'control_response'
                and v.get('response', {}).get('request_id') == 'init')
        response = request({'type': 'control_request', 'request_id': 'quota',
                            'request': {'subtype': 'get_usage'}},
                           lambda v: v.get('type') == 'control_response'
                           and v.get('response', {}).get('request_id') == 'quota')['response']
        if response.get('subtype') != 'success':
            raise ValueError('Claude quota unavailable')
        return response['response']



def claude_quota():
    from datetime import datetime
    from .capacity import windows
    value = claude_usage()
    if not value.get('rate_limits_available') or not isinstance(value.get('rate_limits'), dict):
        raise ValueError('Claude quota requires a normal account login')
    result = []
    for name in ('five_hour', 'seven_day'):
        row = value['rate_limits'].get(name)
        if not row or row.get('utilization') is None or not row.get('resets_at'):
            continue
        reset = datetime.fromisoformat(row['resets_at'].replace('Z', '+00:00'))
        if reset.tzinfo is None:
            raise ValueError('Claude reset needs a timezone')
        result.append(dict(name=name, used_percent=row['utilization'], reset_at=reset.timestamp()))
    if not result:
        raise ValueError('Claude quota windows unavailable')
    return windows(result)


def grok_quota(config=None):
    from .transport import load_config, validate_config
    from . import grok_quota as adapter
    from .capacity import windows
    from pathlib import Path
    config = load_config() if config is None else validate_config(config)
    worker = config.get('grok', {})
    if worker.get('transport') != 'lima':
        return adapter.fetch()
    # Credentials stay in the VM used for actual work. The fixed script reads
    # only quota; it cannot execute prompts or change billing settings.
    import base64
    encoded = base64.b64encode(Path(adapter.__file__).read_bytes()).decode('ascii')
    bootstrap = 'import base64;exec(base64.b64decode('+repr(encoded)+'))'
    result = subprocess.run(['limactl', 'shell', '--workdir=/tmp', worker['vm'],
                             'timeout', '12s', 'python3', '-c', bootstrap],
                            capture_output=True, timeout=15)
    if result.returncode or len(result.stdout) > 10000:
        raise ValueError('Grok worker quota unavailable')
    return windows(json.loads(result.stdout))
