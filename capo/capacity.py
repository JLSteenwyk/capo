"""Private, cross-process subscription observations and bounded worker routing.

Percentages describe provider quota windows, never an estimated token balance.
Unknown capacity is usable, not unlimited. This module never purchases credits.
"""
import fcntl
import json
import math
import os
import time
from contextlib import contextmanager
from pathlib import Path

PROVIDERS = ('claude', 'codex', 'grok')
FRESH_SECONDS = 300
PROBE_SECONDS = 60


def default_home():
    return Path(os.environ.get('CAPO_HOME', str(Path.home()/'.local/share/capo')))


def number(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError('Invalid quota number')
    return value


def windows(value):
    if not isinstance(value, list) or len(value) > 10:
        raise ValueError('Invalid quota windows')
    result = []
    for row in value:
        used, reset = number(row['used_percent']), number(row['reset_at'])
        name = row['name']
        if name not in ('primary', 'secondary', 'five_hour', 'seven_day', 'spend_limit') or used < 0 or reset < 0:
            raise ValueError('Invalid quota window')
        result.append(dict(name=name, used_percent=used, reset_at=reset))
    return result


def codex_windows(result):
    buckets = result.get('rateLimitsByLimitId') or {}
    bucket = buckets.get('codex', result.get('rateLimits'))
    if not isinstance(bucket, dict) or bucket.get('limitId') not in (None, 'codex'):
        return []  # A different model's bucket cannot describe the default worker.
    return windows([dict(name=name, used_percent=bucket[name]['usedPercent'],
                         reset_at=bucket[name]['resetsAt'])
                    for name in ('primary', 'secondary') if bucket.get(name)])


def claude_windows(payload):
    limits = payload.get('rate_limits') or {}
    return windows([dict(name=name, used_percent=limits[name]['used_percentage'],
                         reset_at=limits[name]['resets_at'])
                    for name in ('five_hour', 'seven_day', 'spend_limit') if limits.get(name)])


class Capacity:
    def __init__(self, home=None, clock=None, probes=None):
        self.root = Path(home if home is not None else default_home())/'capacity'
        self.clock = clock or time.time
        if probes is None:
            from .quota_probe import codex_quota
            probes = {'codex': codex_quota}
        self.probes = probes

    def _prepare(self):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)

    @contextmanager
    def _lock(self, name, blocking=True):
        self._prepare()
        fd = os.open(self.root/name, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            yield
        finally:
            os.close(fd)

    def _read(self):
        try:
            value = json.loads((self.root/'observations.json').read_text())
            return value if isinstance(value, dict) else {}
        except (FileNotFoundError, ValueError):
            return {}

    def _update(self, provider, fields):
        if provider not in PROVIDERS:
            raise ValueError('Unknown capacity provider')
        from .conversation import _write
        with self._lock('state.lock'):
            value = self._read()
            row = value.setdefault(provider, {})
            if 'observed_at' not in fields or fields['observed_at'] >= row.get('observed_at', 0):
                if 'windows' in fields:
                    names = {w['name'] for w in fields['windows']}
                    fields['windows'] += [w for w in row.get('windows', [])
                                          if w['name'] not in names and w['used_percent'] >= 100
                                          and w['reset_at'] > self.clock()]
                if 'cooldown_until' in fields:
                    fields['cooldown_until'] = max(fields['cooldown_until'], row.get('cooldown_until', 0))
                row.update(fields)
            _write(self.root/'observations.json', value)

    def observe(self, provider, value, source, observed_at=None):
        if source not in ('codex_app_server', 'claude_statusline'):
            raise ValueError('Unknown capacity source')
        self._update(provider, dict(windows=windows(value), source=source,
                     observed_at=self.clock() if observed_at is None else observed_at))

    def limited(self, provider, reset_at):
        reset_at = number(reset_at)
        self._update(provider, {'cooldown_until': max(self.clock()+1, reset_at)})

    def refresh(self, providers):
        for provider in providers:
            if provider not in self.probes:
                continue
            try:
                with self._lock(provider+'-probe.lock', blocking=False):
                    now = self.clock()
                    if now - self._read().get(provider, {}).get('checked_at', -PROBE_SECONDS) < PROBE_SECONDS:
                        continue
                    try:
                        value = self.probes[provider]()
                        self.observe(provider, value, 'codex_app_server', now)
                        error = None
                    except Exception:
                        # Never persist provider diagnostics, account IDs or credentials.
                        error = 'quota_probe_unavailable'
                    self._update(provider, dict(checked_at=now, probe_error=error))
            except BlockingIOError:
                pass  # A concurrent caller already owns the bounded probe.

    def snapshot(self, providers=PROVIDERS, refresh=False):
        providers = list(dict.fromkeys(providers))
        if any(p not in PROVIDERS for p in providers):
            raise ValueError('Unknown capacity provider')
        if refresh:
            self.refresh(providers)
        now, saved = self.clock(), self._read()
        result = {}
        for provider in providers:
            row = saved.get(provider, {})
            age = now - row.get('observed_at', 0)
            fresh = 0 <= age <= FRESH_SECONDS and 'observed_at' in row
            current = [w for w in row.get('windows', []) if w['reset_at'] > now]
            blocked = [w['reset_at'] for w in current if w['used_percent'] >= 100]
            if row.get('cooldown_until', 0) > now:
                blocked.append(row['cooldown_until'])
            remaining = min((max(0, 100-w['used_percent']) for w in current), default=None) if fresh else None
            result[provider] = dict(
                state='exhausted' if blocked else ('available' if remaining is not None else 'unknown'),
                remaining_percent=0 if blocked else remaining,
                retry_at=max(blocked) if blocked else None,
                observed_at=row.get('observed_at'), source=row.get('source', 'no_quota_interface'),
                fresh=fresh, windows=current if fresh else [], probe_error=row.get('probe_error'))
        return result

    def choose(self, preferred, allowed, refresh=True):
        """Only the host's eligible set can be ranked; never add a provider."""
        from .recovery import RateLimited
        if preferred not in allowed or not allowed:
            raise ValueError('Preferred worker must be in the allowed set')
        snapshot = self.snapshot(allowed, refresh=refresh)
        eligible = [p for p in allowed if snapshot[p]['state'] != 'exhausted']
        if not eligible:
            raise RateLimited(min(snapshot[p]['retry_at'] for p in allowed))
        # Preserve task-fit preference unless it is exhausted/low, or another
        # measured worker has substantially more headroom. Unknown is neutral.
        def score(p):
            remaining = snapshot[p]['remaining_percent']
            return (50 if remaining is None else remaining) + (10 if p == preferred else 0)
        selected = max(eligible, key=score)
        return selected, dict(preferred=preferred, selected=selected,
                             reason='capacity_headroom' if selected != preferred else 'preferred_worker',
                             checked_at=self.clock(), capacity=snapshot)

    def check(self, provider):
        self.choose(provider, [provider], refresh=False)

    def tools(self):
        from .contracts import object_schema
        from .research_tools import ReadTool
        return [ReadTool('workers.capacity',
            'Read subscription capacity before delegation or explain worker availability. '
            'Quota percentages are provider windows, not token counts or guarantees. '
            'Unknown means no fresh measurement. Claude remains chief; routing cannot expand permissions.',
            object_schema({}), lambda: self.snapshot(refresh=True))]
