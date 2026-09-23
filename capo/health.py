"""Private connection observations and read-only automation health checks."""
from contextlib import closing
import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .contracts import object_schema
from .research_tools import ReadTool


def diagnosis(exc):
    from .providers import AuthenticationError
    from .recovery import RateLimited
    if isinstance(exc, AuthenticationError) or type(exc).__name__ == 'RefreshError':
        return 'authentication', 'Reconnect this service; automatic refresh could not restore access.'
    if isinstance(exc, PermissionError):
        return 'permission', 'Review the account permissions for this service.'
    if isinstance(exc, RateLimited):
        return 'rate_limit', 'Wait for the service limit to reset; a later check can retry.'
    if isinstance(exc, (ConnectionError, TimeoutError)) or type(exc).__name__ in ('ConnectionError', 'Timeout', 'ReadTimeout'):
        return 'temporary', 'A later read-only check will retry. No action should be repeated without checking its result.'
    return 'unavailable', 'Inspect the connection and saved run details. Completion is not confirmed.'


class Health:
    def __init__(self, home, config):
        from .capabilities import owner_key
        self.home, self.config = Path(home), config
        self.root = self.home/'health'/hashlib.sha256(owner_key(config).encode()).hexdigest()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.root/'checks.sqlite3'
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS checks(service TEXT PRIMARY KEY, data TEXT)')
        self.path.chmod(0o600)

    def record(self, service, exc=None, now=None):
        now = now or datetime.now(timezone.utc)
        status, action = diagnosis(exc) if exc else ('healthy', '')
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            row = db.execute('SELECT data FROM checks WHERE service=?', (service,)).fetchone()
            previous = json.loads(row[0]) if row else {}
            result = {'service': service, 'status': status, 'next_action': action,
                      'checked_at': now.isoformat(),
                      'last_success': now.isoformat() if exc is None else previous.get('last_success'),
                      'consecutive_failures': previous.get('consecutive_failures', 0)+1 if exc else 0}
            db.execute('INSERT OR REPLACE INTO checks VALUES (?,?)', (service, json.dumps(result)))
        return result

    def check(self, service):
        if service not in ('gmail', 'calendar'):
            raise ValueError('Choose gmail or calendar')
        if not self.config.get(service, {}).get('enabled'):
            return {'service': service, 'status': 'disabled', 'next_action': 'Enable this integration to check it.'}
        try:
            if service == 'gmail':
                from .gmail import Gmail
                client = Gmail(drafts=self.config.get('gmail', {}).get('drafts', False))
                client.get('messages', {'maxResults': 1})
                if client.drafts_enabled: client.get('drafts', {'maxResults': 1})
            else:
                from .calendar import GoogleCalendar
                now = datetime.now(timezone.utc)
                GoogleCalendar().events(now.isoformat(), (now+timedelta(minutes=1)).isoformat())
            return self.record(service)
        except Exception as exc:
            return self.record(service, exc)

    def status(self, now=None):
        now = now or datetime.now(timezone.utc)
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            checks = {s: json.loads(data) for s, data in db.execute('SELECT service,data FROM checks')}
        for name in ('gmail', 'calendar'):
            if not self.config.get(name, {}).get('enabled'):
                checks[name] = {'service': name, 'status': 'disabled'}
            else:
                checks.setdefault(name, {'service': name, 'status': 'not_checked'})
        for check in checks.values():
            check['stale'] = bool(check.get('checked_at') and
                now-datetime.fromisoformat(check['checked_at']) > timedelta(hours=2))
        from .slack_outbox import pending
        return {'connections': list(checks.values()), 'automations': self.automations(now),
                'unconfirmed_slack_replies':pending(self.home),
                'requests':self.requests(now),
                'coverage': 'Saved connection observations, not continuous monitoring. Stale or untested is not healthy. '
                            'Automation checks use saved receipts and current schedules; no writes are replayed. '
                            'Use health.check for a fresh Google connection probe. Other provider login status is available through capacity tools.'}

    def requests(self, now):
        from .reliability import review
        return review(self.home,self.config,now)

    def automations(self, now):
        from .capabilities import owner_key
        from .digest import DigestStore, scope, wall, weekdays
        from .schedules import Schedules, matches_day
        from .team import ROLES, active_roles
        if not all(self.config.get(k) for k in ('team_id', 'channel_id', 'owner_user_id')): return []
        specs = []
        retired = set(ROLES)-set(active_roles(self.config))
        for s in Schedules(self.home, owner_key(self.config)).list()['schedules']:
            if s['enabled'] and s.get('agent') not in retired:
                specs.append(('scheduled', s['id'], s['title'], s, None))
        db = DigestStore(self.home)
        try: prefs = db.preferences(scope(self.config))
        finally: db.close()
        if prefs['enabled']: specs.append(('', '', 'Morning digest', prefs, None))
        heartbeat = self.config.get('heartbeat', {})
        if heartbeat.get('enabled'):
            h = dict(heartbeat, timezone=heartbeat.get('timezone', self.config.get('calendar', {}).get('timezone', 'America/Los_Angeles')))
            specs.append(('heartbeat', '', 'Capo hourly check', h, range(h.get('start_hour',10), h.get('end_hour',16)+1)))
        results = []
        for folder, sid, title, s, hours in specs:
            zone = ZoneInfo(s['timezone']); latest = None
            for offset in range(32):
                day = now.astimezone(zone).date()-timedelta(days=offset)
                if sid:
                    if not matches_day(s, day): continue
                elif str(day.weekday()) not in weekdays(s): continue
                times = [f'{hour:02d}:00' for hour in hours] if hours is not None else [s['time']]
                for time in reversed(times):
                    due = wall(day, time, s['timezone'])
                    if due > now: continue
                    if s.get('updated_at') and due < datetime.fromisoformat(s['updated_at']): continue
                    latest = due; break
                if latest: break
            if latest is None: continue
            path = self.home/folder/'digest/digest.sqlite3'
            run = None
            if path.exists():
                with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)) as db:
                    rows = db.execute('SELECT data FROM runs WHERE scope=? ORDER BY updated DESC LIMIT 500', (scope(self.config),))
                    for (data,) in rows:
                        candidate = json.loads(data)
                        if candidate.get('preview'): continue
                        if sid and candidate.get('schedule_id') != sid: continue
                        if sid and candidate.get('revision') != s['revision']: continue
                        if hours is not None:
                            if not candidate['key'].endswith(latest.strftime('%Y-%m-%dT%H')): continue
                        elif candidate.get('day') != (latest.astimezone(timezone.utc) if sid else latest).date().isoformat(): continue
                        run = candidate; break
            window = int(s['catch_up_hours'])*60 if sid else int(s.get('window_minutes', 10 if hours is not None else 120))
            deadline = (latest+timedelta(minutes=window)).timestamp()
            state = (run or {}).get('status', 'not_started')
            problems = (run or {}).get('report', {}).get('blockers', [])
            outcome = (run or {}).get('outcome_status')
            if state in ('failed','expired') or problems or outcome == 'partial' or (state=='sending' and (run or {}).get('delivery_error')=='unconfirmed'): state = 'needs_attention'
            elif state not in ('sent','quiet','cancelled') and now.timestamp() > (run or {}).get('deadline', deadline): state = 'missed'
            results.append({'title': title, 'schedule_id': sid, 'due_at': latest.isoformat(), 'status': state,
                            'next_action': (' '.join(problems[:2]) or (run or {}).get('error_summary') or
                                            ('Slack delivery is unconfirmed. Reconcile the saved message; do not resend blindly.' if (run or {}).get('delivery_error')=='unconfirmed' else '') or
                                            'This check is incomplete. Capo needs to inspect its saved results before retrying.')
                                           if state in ('needs_attention','missed') else '',
                            'coverage': 'Current schedule; latest expected occurrence within 32 days, latest 500 saved runs.'})
        return results

    def tools(self):
        return [ReadTool('health.status', 'Read owner-scoped pending replies, incomplete request outcomes, connection freshness and missing/failed scheduled reports, including digest and hourly checks. Does not run probes or replay actions.', object_schema({}), self.status),
                ReadTool('health.check', 'Perform one bounded read-only Google connection probe, using normal automatic token refresh. Records health privately; never opens login pages, sends messages, or retries writes.', object_schema({'service': {'type':'string','enum':['gmail','calendar']}}), self.check)]
