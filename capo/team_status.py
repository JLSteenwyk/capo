"""Shared team visibility from persisted assignments and execution receipts."""
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .contracts import object_schema
from .digest import scope, wall
from .research_tools import ReadTool
from .schedules import Schedules, matches_day
from .team import ROLES


def next_check(schedule, now):
    if not schedule['enabled']:
        return None
    zone = ZoneInfo(schedule['timezone'])
    first=now.astimezone(zone).date()
    if schedule.get('anchor_date'):first=max(first,datetime.fromisoformat(schedule['anchor_date']).date())
    for offset in range(7*int(schedule.get('interval_days','1'))+1):
        day = first + timedelta(days=offset)
        due = wall(day, schedule['time'], schedule['timezone'])
        if matches_day(schedule,day) and due > now:
            return due.isoformat()
    return None


class TeamStatus:
    def __init__(self, home, config):
        self.home, self.config = Path(home), config

    def status(self, now=None):
        from .capabilities import owner_key
        now = now or datetime.now(timezone.utc)
        schedules = Schedules(self.home, owner_key(self.config)).list()['schedules']
        names = {'capo': 'Capo', **{key: value[0] for key, value in ROLES.items()}, 'coding': 'Coding Agent'}
        teams = {key: {'agent': key, 'name': name, 'assignments': []} for key, name in names.items()}
        path = self.home / 'scheduled/digest/digest.sqlite3'
        db = sqlite3.connect(f'file:{path}?mode=ro', uri=True) if path.exists() else None
        try:
            for schedule in schedules:
                last = None
                if db:
                    row = db.execute("SELECT data FROM runs WHERE scope=? AND json_extract(data, '$.schedule_id')=? "
                        "ORDER BY json_extract(data, '$.created') DESC LIMIT 1",
                        (scope(self.config), schedule['id'])).fetchone()
                    if row: last = json.loads(row[0])
                report = (last or {}).get('report', {})
                item = dict(id=schedule['id'], title=schedule['title'], request=schedule['request'],
                    enabled=schedule['enabled'], delivery=schedule.get('delivery', 'always'),
                    tool_prefixes=schedule.get('tool_prefixes', []),
                    interval_days=schedule.get('interval_days','1'), anchor_date=schedule.get('anchor_date',''),
                    next_check=next_check(schedule, now), last_status=(last or {}).get('status', 'not_run'),
                    last_started=(last or {}).get('created'), last_checked=(last or {}).get('checked_at'),
                    report_revision=(last or {}).get('revision'), current_revision=schedule['revision'],
                    blockers=report.get('blockers', []), findings=report.get('findings', []),
                    coverage=report.get('coverage', ''), error=(last or {}).get('error_summary', ''))
                teams[schedule.get('agent', 'capo')]['assignments'].append(item)
        finally:
            if db: db.close()
        return {'agents': list(teams.values()), 'heartbeat': {
            key: self.config.get('heartbeat', {}).get(key) for key in ('enabled', 'start_hour', 'end_hour', 'timezone')},
            'coverage': 'Standing assignments and their latest run receipts. not_run is not a successful check; quiet means no new alerts in the reported coverage. An agent without assignments is available on demand. Other interactive coding jobs are tracked separately by development tools. Historical reports may predate an assignment edit.'}

    def tools(self):
        return [ReadTool('team.status', 'Show each agent’s standing assignments, most recent checks, next scheduled checks, findings and blockers. Also use for full saved monitoring results. No new checks are started.', object_schema({}), self.status)]
