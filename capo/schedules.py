"""Owner-configured recurring read-only requests, independent of task category."""
import hashlib
import json
import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from .contracts import TEXT, TEXTS, object_schema, validate
from .digest import wall
from .tasks import Tasks
from .research_tools import ReadTool

FIELDS=object_schema({'title':TEXT,'request':TEXT,'weekdays':TEXTS,'time':TEXT,'timezone':TEXT,
                      'enabled':{'type':'boolean'},'catch_up_hours':{'type':'string','enum':['1','6','12','24']}})

# Optional fields keep existing schedules and callers compatible.
FIELDS['properties'].update({
    'agent': {'type': 'string', 'enum': ['capo', 'money_saver', 'style_assistant', 'shopping_assistant', 'coding']},
    'delivery': {'type': 'string', 'enum': ['always', 'changes']},
    'tool_prefixes': TEXTS,
    'interval_days': TEXT,
    'anchor_date': TEXT,
})


class Schedules(Tasks):
    def __init__(self,home,owner):
        super().__init__(home,owner)
        with self.connection() as db:
            db.execute('CREATE TABLE IF NOT EXISTS schedules(id TEXT PRIMARY KEY, data TEXT NOT NULL)')

    def list(self):
        with self.connection() as db:
            return {'schedules':[json.loads(r[0]) for r in db.execute('SELECT data FROM schedules ORDER BY id')]}

    def save(self,id,expected_revision,fields,operation_id):
        validate(fields,FIELDS)
        prefixes=fields.get('tool_prefixes',[])
        if len(prefixes)>20 or any(not re.fullmatch(r'[a-z][a-z0-9_]*\.',p) for p in prefixes):
            raise ValueError('Use capability prefixes such as github. to narrow the read-only tools')
        if not operation_id:raise ValueError('Host action receipt required')
        if not fields['title'].strip() or len(fields['title'])>200 or not fields['request'].strip() or len(fields['request'])>4000:
            raise ValueError('Provide a short title and bounded request')
        if not fields['weekdays'] or not set(fields['weekdays'])<=set('0123456') or len(set(fields['weekdays']))!=len(fields['weekdays']):
            raise ValueError('Choose unique weekdays 0 Monday through 6 Sunday')
        if datetime.strptime(fields['time'],'%H:%M').strftime('%H:%M')!=fields['time']:raise ValueError('Use HH:MM')
        ZoneInfo(fields['timezone'])
        request=json.dumps(['schedule',id,expected_revision,fields],sort_keys=True)
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            receipt=db.execute('SELECT request,result FROM receipts WHERE id=?',(operation_id,)).fetchone()
            if receipt:
                if receipt[0]!=request:raise ValueError('Action receipt changed')
                return json.loads(receipt[1])
            previous=None
            if id:
                row=db.execute('SELECT data FROM schedules WHERE id=?',(id,)).fetchone()
                if not row:raise ValueError('Schedule not found')
                previous=json.loads(row[0])
                if expected_revision!=str(previous['revision']):raise ValueError('Schedule changed; read it again')
            elif expected_revision:raise ValueError('New schedules have no revision')
            else:id=hashlib.sha256(operation_id.encode()).hexdigest()
            fields = dict(fields)
            for name, default in (('agent', 'capo'), ('delivery', 'always'), ('tool_prefixes', []), ('interval_days', '1'), ('anchor_date', '')):
                fields.setdefault(name, (previous or {}).get(name, default))
            interval=fields['interval_days']
            if not re.fullmatch(r'[1-9][0-9]?',interval) or not 1<=int(interval)<=28:
                raise ValueError('Use an interval from 1 to 28 calendar days')
            anchor=fields['anchor_date']
            if anchor:
                parsed=date.fromisoformat(anchor)
                if parsed.isoformat()!=anchor:raise ValueError('Use an ISO anchor date: YYYY-MM-DD')
                if not any(matches_day(fields,parsed+timedelta(days=n)) for n in range(7*int(interval))):
                    raise ValueError('The chosen weekdays never occur on this interval')
            elif interval!='1':raise ValueError('An interval needs an anchor date')
            now=datetime.now(timezone.utc).isoformat()
            result={'schedule':dict(fields,id=id,revision=previous['revision']+1 if previous else 1,
                                    created_at=previous['created_at'] if previous else now,updated_at=now), 'saved':True}
            db.execute('INSERT OR REPLACE INTO schedules VALUES (?,?)',(id,json.dumps(result['schedule'])))
            db.execute('INSERT INTO receipts VALUES (?,?,?)',(operation_id,request,json.dumps(result)))
            return result

    def tools(self):
        return [ReadTool('schedules.list','List owner-configured recurring read-only requests and their revisions.',object_schema({}),self.list),
                ReadTool('schedules.save','Create/edit/pause a recurring read-only request after the owner chooses its schedule. Runs the shared tools. Optional agent assigns responsibility; delivery=changes reports only new findings or blockers, always delivers each result. Optional tool_prefixes narrows available read capabilities (for example github. and clock.); an empty list uses all read tools. Existing assignments retain these options when omitted. Optional interval_days (1–28, default 1) and anchor_date (YYYY-MM-DD) repeat on local calendar days from that date; use 2 with adjacent anchors for alternating days, 14 with anchors seven days apart for alternating weekly checks. Weekdays 0 Monday through 6 Sunday further restrict those dates. Empty id/revision creates; edits require current revision. Set enabled=false to stop. Does not alter the existing morning digest or hourly checks. Scheduled requests cannot send email or mutate tasks/calendar.',
                         object_schema({'id':TEXT,'expected_revision':TEXT,'fields':FIELDS}),self.save,mutates=True)]


def matches_day(schedule, day):
    if str(day.weekday()) not in schedule['weekdays']:return False
    anchor=schedule.get('anchor_date','')
    if not anchor:return True
    elapsed=(day-date.fromisoformat(anchor)).days
    return elapsed>=0 and elapsed%int(schedule.get('interval_days','1'))==0


def due_slot(schedule,now):
    if not schedule['enabled']:return None
    local=now.astimezone(ZoneInfo(schedule['timezone']))
    for days in range(8):
        day=local.date()-timedelta(days=days)
        if not matches_day(schedule,day):continue
        due=wall(day,schedule['time'],schedule['timezone']).astimezone(timezone.utc)
        if due>now:continue
        if due<datetime.fromisoformat(schedule['created_at']):return None
        deadline=due+timedelta(hours=int(schedule['catch_up_hours']))
        return (due,deadline) if now<deadline else None
    return None
