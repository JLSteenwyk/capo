"""Read Codex's supported account quota RPC without creating a model turn."""
import json
import os
import selectors
import signal
import subprocess
import tempfile
import time


def codex_quota(timeout=8, popen=subprocess.Popen):
    from .capacity import codex_windows
    with tempfile.TemporaryDirectory(prefix='capo-quota-') as cwd:
        process = popen(['codex', 'app-server', '--listen', 'stdio://'], cwd=cwd,
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL, start_new_session=True)
        selector = selectors.DefaultSelector()
        deadline, pending, total = time.monotonic()+timeout, b'', 0
        try:
            selector.register(process.stdout, selectors.EVENT_READ)
            def send(value):
                process.stdin.write((json.dumps(value)+'\n').encode())
                process.stdin.flush()
            def receive(id):
                nonlocal pending, total
                while True:
                    while b'\n' in pending:
                        line, pending = pending.split(b'\n', 1)
                        value = json.loads(line)
                        if value.get('id') == id:
                            if 'error' in value or 'result' not in value:
                                raise ValueError('Quota RPC unavailable')
                            return value['result']
                    remaining = deadline-time.monotonic()
                    if remaining <= 0 or not selector.select(remaining):
                        raise TimeoutError('Quota probe timed out')
                    chunk = os.read(process.stdout.fileno(), 65536)
                    if not chunk:
                        raise ValueError('Quota probe ended early')
                    total += len(chunk)
                    if total > 1000000:
                        raise ValueError('Quota response too large')
                    pending += chunk
            send({'id': 1, 'method': 'initialize', 'params': {
                'clientInfo': {'name': 'capo_quota', 'version': '1.0.0'}}})
            receive(1)
            send({'method': 'initialized', 'params': {}})
            send({'id': 2, 'method': 'account/rateLimits/read'})
            return codex_windows(receive(2))
        finally:
            selector.close()
            # This is our own child/process group, never a PID from saved state.
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
