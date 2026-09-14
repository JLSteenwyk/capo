"""Durable, nonblocking recovery for bounded reasoning calls, never tool writes."""
import fcntl
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

from .contracts import validate
from .conversation import _write
from .process import CleanupUncertain, reconcile_local


class RateLimited(RuntimeError):
    def __init__(self, reset_at):
        super().__init__('Provider rate limit reached')
        import math
        self.reset_at = float(reset_at)
        if not math.isfinite(self.reset_at):
            raise ValueError('Invalid provider reset time')


class RetryLater(RuntimeError):
    def __init__(self, retry_at, reason='Temporary provider problem'):
        super().__init__(reason)
        self.retry_at = retry_at


class RecoveryStopped(RuntimeError):
    pass


def policy(value=None):
    value = {} if value is None else value
    if not isinstance(value, dict) or set(value) - {'max_attempts', 'base_delay', 'fallbacks'}:
        raise ValueError('Invalid recovery policy')
    attempts = value.get('max_attempts', 3)
    delay = value.get('base_delay', 30)
    fallbacks = value.get('fallbacks', {})
    if type(attempts) is not int or not 1 <= attempts <= 5:
        raise ValueError('Recovery attempts must be between one and five')
    if type(delay) is not int or not 1 <= delay <= 3600:
        raise ValueError('Recovery delay must be between one second and one hour')
    if not isinstance(fallbacks, dict):
        raise ValueError('Invalid fallback map')
    for provider, workers in fallbacks.items():
        if provider not in ('claude', 'codex', 'grok') or not isinstance(workers, list):
            raise ValueError('Invalid fallback provider')
        if any(type(w) is not str or w not in ('claude', 'codex', 'grok') or w == provider for w in workers) or len(set(workers)) != len(workers):
            raise ValueError('Invalid fallback workers')
    return dict(max_attempts=attempts, base_delay=delay, fallbacks=fallbacks)


def failure(exc, now):
    """Expose only fixed error categories; raw provider errors stay private."""
    from .providers import AuthenticationError
    from .effects import UncertainEffect
    if isinstance(exc, AuthenticationError):
        return 'authentication', None
    if isinstance(exc, (PermissionError, UncertainEffect, CleanupUncertain)):
        return 'permission_or_unconfirmed_action', None
    if isinstance(exc, RateLimited):
        return 'rate_limit', max(now, exc.reset_at)
    if isinstance(exc, FileNotFoundError):
        return 'unavailable', now
    if isinstance(exc, (ConnectionError, TimeoutError, subprocess.TimeoutExpired)):
        return 'temporary', now
    return 'failed', None


class RecoveringProvider:
    """One attempt per call; callers schedule RetryLater without sleeping.

    A stable operation directory retains input identity, attempts and the result.
    Fallbacks are explicit host policy; they receive the identical schema/prompt.
    This wrapper never invokes or retries a registered mutation tool.
    """
    def __init__(self, provider, config=None, clock=None):
        self.provider = provider
        self.policy = policy(config)
        self.clock = clock or time.time

    def call(self, provider, prompt, schema, cwd, directory, images=None):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(directory/'recovery.lock', os.O_CREAT | os.O_RDWR, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RetryLater(self.clock() + 5, 'Provider call is already running') from None
            return self._call(provider, prompt, schema, cwd, directory, images)
        finally:
            os.close(fd)

    def _call(self, provider, prompt, schema, cwd, directory, images):
        now = self.clock()
        identity = hashlib.sha256(json.dumps([provider, prompt, schema, images], sort_keys=True).encode()).hexdigest()
        path = directory/'recovery.json'
        state = json.loads(path.read_text()) if path.exists() else {
            'identity': identity, 'policy': self.policy, 'attempts': [], 'status': 'ready', 'retry_at': 0}
        if state['identity'] != identity or state['policy'] != self.policy:
            raise RecoveryStopped('Recovery input or policy changed')
        if (directory/'cancelled.json').exists():
            state['status'] = 'cancelled'
            _write(path, state)
        if state['status'] == 'done':
            validate(state['result'], schema)
            return state['result']
        if state['status'] in ('failed', 'cancelled'):
            raise RecoveryStopped('Provider work is ' + state['status'])
        if now < state['retry_at']:
            raise RetryLater(state['retry_at'])
        if state['status'] == 'running':
            # A lost caller does not prove its worker stopped. Probe before retrying.
            previous = directory / str(len(state['attempts']))
            if (previous/'remote.json').exists():
                from .transport import remote_status
                status = remote_status(json.loads((previous/'remote.json').read_text()))
                if status.get('state') not in ('finished', 'failed', 'missing'):
                    raise RetryLater(now + 30, 'Recorded worker is still running')
            if (previous/'process.json').exists():
                reconcile_local(previous)
            if (previous/'response.json').exists():
                result = json.loads((previous/'response.json').read_text())
                validate(result, schema)
                state.update(status='done', result=result)
                _write(path, state)
                return result
            state.update(status='waiting', retry_at=now + self.policy['base_delay'])
            _write(path, state)
            raise RetryLater(state['retry_at'], 'Interrupted reasoning will resume')
        count = len(state['attempts'])
        if count >= self.policy['max_attempts']:
            state['status'] = 'failed'
            _write(path, state)
            raise RecoveryStopped('Provider retry limit reached')
        choices = [provider] + self.policy['fallbacks'].get(provider, [])
        selected = choices[min(count, len(choices)-1)]
        # Native image support remains provider-specific; never discard images.
        if images:
            selected = provider
        attempt = {'provider': selected, 'started_at': now}
        state['attempts'].append(attempt)
        state['status'] = 'running'
        _write(path, state)
        try:
            kwargs = {'images': images} if images else {}
            result = self.provider.call(selected, prompt, schema, cwd, directory/str(count+1), **kwargs)
            validate(result, schema)
        except Exception as exc:
            category, retry_at = failure(exc, self.clock())
            attempt.update(category=category, finished_at=self.clock())
            state.update(status='failed' if retry_at is None or count+1 >= self.policy['max_attempts'] else 'waiting',
                         retry_at=max(retry_at or 0, self.clock() + min(3600, self.policy['base_delay'] * 2**count)))
            _write(path, state)
            if state['status'] == 'waiting':
                raise RetryLater(state['retry_at'], category) from None
            raise
        if (directory/'cancelled.json').exists():
            state['status'] = 'cancelled'
            _write(path, state)
            raise RecoveryStopped('Provider work is cancelled')
        state.update(status='done', result=result)
        _write(path, state)
        return result
