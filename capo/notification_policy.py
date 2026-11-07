"""Owner-scoped controls for unsolicited hourly notifications only."""
import hashlib
import json
import re
import sqlite3
from pathlib import Path
from contextlib import closing
from zoneinfo import ZoneInfo
from .contracts import TEXT,object_schema
from .research_tools import ReadTool

DEFAULT={'quiet_start':'','quiet_end':'','timezone':'America/Los_Angeles','importance':'all'}
SCHEMA=object_schema({'quiet_start':TEXT,'quiet_end':TEXT,'timezone':TEXT,
                      'importance':{'type':'string','enum':['all','important']}})


def validate(value):
    if set(value)!=set(DEFAULT):raise ValueError('Supply all notification preference fields')
    for key in ('quiet_start','quiet_end'):
        if not isinstance(value[key],str) or (value[key] and not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',value[key])):raise ValueError('Use HH:MM or empty quiet times')
    if bool(value['quiet_start'])!=bool(value['quiet_end']) or (value['quiet_start'] and value['quiet_start']==value['quiet_end']):raise ValueError('Quiet start/end must define a nonempty window')
    ZoneInfo(value['timezone'])
    if value['importance'] not in ('all','important'):raise ValueError('Invalid notification importance')
    return dict(value)


class NotificationPolicy:
    def __init__(self,home,owner,origin=None):
        root=Path(home)/'notification-preferences'/hashlib.sha256(owner.encode()).hexdigest()
        root.mkdir(mode=0o700,parents=True,exist_ok=True);root.chmod(0o700)
        self.path=root/'preferences.sqlite3';self.origin=origin
        with closing(sqlite3.connect(self.path)) as db,db:
            db.executescript('CREATE TABLE IF NOT EXISTS preferences(id INTEGER PRIMARY KEY,revision INTEGER,data TEXT); CREATE TABLE IF NOT EXISTS receipts(id TEXT PRIMARY KEY,request TEXT,result TEXT);')
        self.path.chmod(0o600)

    def read(self):
        with closing(sqlite3.connect(self.path)) as db:
            row=db.execute('SELECT revision,data FROM preferences WHERE id=1').fetchone()
        return {'revision':str(row[0]) if row else '', 'preferences':json.loads(row[1]) if row else dict(DEFAULT),
                'coverage':'Controls unsolicited hourly checks only. Explicit reminders, direct replies and owner-scheduled deliveries bypass this filter. High-priority task alerts, selected imminent calendar events and host-classified owner-action problems bypass quiet time. Suppressed checks are recorded and the next hourly check reevaluates current evidence.'}

    def change(self,revision,preferences,operation_id):
        if not self.origin or not self.origin.get('text'):raise PermissionError('An explicit owner request is required')
        preferences=validate(preferences)
        request=json.dumps([revision,preferences,self.origin],sort_keys=True)
        with closing(sqlite3.connect(self.path)) as db,db:
            db.execute('BEGIN IMMEDIATE')
            prior=db.execute('SELECT request,result FROM receipts WHERE id=?',(operation_id,)).fetchone()
            if prior:
                if prior[0]!=request:raise ValueError('Notification receipt changed')
                return json.loads(prior[1])
            row=db.execute('SELECT revision FROM preferences WHERE id=1').fetchone()
            if revision!=(str(row[0]) if row else ''):raise ValueError('Preferences changed; read them again')
            updated=(row[0]+1) if row else 1
            db.execute('INSERT OR REPLACE INTO preferences VALUES(1,?,?)',(updated,json.dumps(preferences)))
            result={'saved':True,'verified':True,'revision':str(updated),'preferences':preferences}
            db.execute('INSERT INTO receipts VALUES(?,?,?)',(operation_id,request,json.dumps(result)))
        return result

    def quiet_reason(self,now,important=False):
        if important:return ''
        p=self.read()['preferences'];local=now.astimezone(ZoneInfo(p['timezone'])).strftime('%H:%M')
        a,b=p['quiet_start'],p['quiet_end']
        if a and ((a<=local<b) if a<b else (local>=a or local<b)):return 'owner_quiet_period'
        if p['importance']=='important':return 'owner_important_only'
        return ''

    def tools(self):
        return [ReadTool('notifications.read','Read actual hourly-notification settings and revision. Distinguish operative settings from preference quotes. Direct replies and explicit reminders are preserved.',object_schema({}),self.read),
                ReadTool('notifications.change','Apply an explicitly owner-requested quiet period or importance filter to unsolicited hourly checks. Read current revision first. All fields required; empty quiet start/end disables quiet periods. Does not change schedules, reminders or permissions. Never infer a change from silence.',object_schema({'revision':TEXT,'preferences':SCHEMA}),self.change,mutates=True)]


def filter_hourly(manager,run,now):
    if run.get('status')!='ready' or ':heartbeat:' not in run.get('key',''):return True
    from .heartbeat import settings
    policy=settings(manager.service.config)
    if policy['notify_hour'] is not None and now.astimezone(ZoneInfo(policy['timezone'])).hour!=policy['notify_hour']:
        run.update(status='quiet',notification_reason='outside_owner_notification_hour')
        manager.db.save(run,now)
        return False
    from .capabilities import owner_key
    from .tasks import Tasks
    owner=owner_key(manager.service.config)
    notices=run.get('payload',{}).get('task_notices',[]);important=run.get('payload',{}).get('host_important') is True
    tasks=Tasks(manager.service.store.home,owner)
    for notice in notices:
        try:task=tasks.get(notice['task_id'])
        except (ValueError,KeyError):continue
        if task['priority']=='high' and task['status'] in ('open','waiting'):important=True
    reason=NotificationPolicy(manager.service.store.home,owner).quiet_reason(now,important)
    if not reason:return True
    run.update(status='quiet',notification_reason=reason)
    manager.db.save(run,now)
    return False


def important_evidence(items,selected_ids,now):
    """Urgency comes from inspected host evidence, never model-written prose."""
    from datetime import datetime
    for item in items:
        if item.get('id') not in selected_ids:continue
        if item.get('kind')=='personal_task' and item.get('priority')=='high':return True
        if item.get('kind')=='connection':
            from .issue_text import describe
            if describe(item)[1]:return True
        if item.get('kind')=='calendar':
            value=item.get('start',{}).get('dateTime')
            if value:
                try:delta=(datetime.fromisoformat(value.replace('Z','+00:00'))-now).total_seconds()
                except (ValueError,TypeError):continue
                if 0<=delta<=7200:return True
    return False
