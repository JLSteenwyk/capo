"""One bounded follow-up for a failed read-only scheduled occurrence."""
import json
from zoneinfo import ZoneInfo


def queue_followups(db, owner, schedules, now):
    """Keep original receipts; a follow-up is a separate, once-only inspection.

    Only today's enabled, unchanged assignments qualify, within six hours of
    their original start and after a thirty-minute cooldown. A follow-up gets
    fifteen minutes and the normal tool limits. It cannot spawn another one.
    """
    latest={}
    for (data,) in db.db.execute('SELECT data FROM runs WHERE scope=? ORDER BY updated DESC, rowid DESC LIMIT 500',(owner,)):
        run=json.loads(data)
        if run.get('preview'):continue
        latest.setdefault(run.get('schedule_id'),run)
    for sid,run in latest.items():
        schedule=schedules.get(sid)
        if not schedule or not schedule['enabled'] or run.get('revision')!=schedule['revision']:continue
        if run.get('recovery_of') or run['status'] not in ('sent','quiet','failed','expired'):continue
        failed=run['status'] in ('failed','expired') or run.get('outcome_status')=='partial' or bool(run.get('report',{}).get('blockers'))
        if not failed:continue
        local=now.astimezone(ZoneInfo(schedule['timezone']))
        if str(local.weekday()) not in schedule['weekdays']:continue
        from datetime import datetime,timezone
        started=datetime.fromtimestamp(run['created'],timezone.utc)
        if started.astimezone(local.tzinfo).date()!=local.date():continue
        age=now.timestamp()-run['created']
        if not 1800<=age<=21600:continue
        key=run['key']+':followup'
        db.create(dict(key=key,scope=owner,day=run['day'],status='queued',
            created=now.timestamp(),deadline=now.timestamp()+900,
            identity=run['identity'],schedule_id=sid,revision=run['revision'],
            request=run['request'],title=run['title'],attempts=0,recovery_of=run['key']),now)
