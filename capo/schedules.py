"""Owner-configured recurring read-only requests, independent of task category."""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from .contracts import TEXT, TEXTS, object_schema, validate
from .digest import wall
from .tasks import Tasks
from .research_tools import ReadTool

FIELDS=object_schema({'title':TEXT,'request':TEXT,'weekdays':TEXTS,'time':TEXT,'timezone':TEXT,
                      'enabled':{'type':'boolean'},'catch_up_hours':{'type':'string','enum':['1','6','12','24']}})


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
            now=datetime.now(timezone.utc).isoformat()
            result={'schedule':dict(fields,id=id,revision=previous['revision']+1 if previous else 1,
                                    created_at=previous['created_at'] if previous else now,updated_at=now), 'saved':True}
            db.execute('INSERT OR REPLACE INTO schedules VALUES (?,?)',(id,json.dumps(result['schedule'])))
            db.execute('INSERT INTO receipts VALUES (?,?,?)',(operation_id,request,json.dumps(result)))
            return result

    def tools(self):
        return [ReadTool('schedules.list','List owner-configured recurring read-only requests and their revisions.',object_schema({}),self.list),
                ReadTool('schedules.save','Create/edit/pause a recurring read-only request after the owner chooses its schedule. Runs the shared tools and sends one Slack result. Weekdays 0 Monday through 6 Sunday. Empty id/revision creates; edits require current revision. Set enabled=false to stop. Does not alter the existing morning digest or hourly checks. Scheduled requests cannot send email or mutate tasks/calendar.',
                         object_schema({'id':TEXT,'expected_revision':TEXT,'fields':FIELDS}),self.save,mutates=True)]


def due_slot(schedule,now):
    if not schedule['enabled']:return None
    local=now.astimezone(ZoneInfo(schedule['timezone']))
    for days in range(8):
        day=local.date()-timedelta(days=days)
        if str(day.weekday()) not in schedule['weekdays']:continue
        due=wall(day,schedule['time'],schedule['timezone']).astimezone(timezone.utc)
        if due>now:continue
        if due<datetime.fromisoformat(schedule['created_at']):return None
        deadline=due+timedelta(hours=int(schedule['catch_up_hours']))
        return (due,deadline) if now<deadline else None
    return None
