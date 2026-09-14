"""Personal task evidence and cross-digest notification deduplication."""
import hashlib
import json
import sqlite3
from datetime import datetime,timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .capabilities import owner_key
from .digest import scope
from .tasks import Tasks


def notice_id(task):
    return 'personal-task:'+hashlib.sha256(json.dumps([task[k] for k in
        ('id','title','due_at','remind_at','status','priority','waiting_on')],sort_keys=True).encode()).hexdigest()


def personal_tasks(home,config,now,horizon_days=7,heartbeat=False):
    if home is None:return []
    owner=owner_key(config);root=Path(home)/'tasks'/hashlib.sha256(owner.encode()).hexdigest()
    if not (root/'tasks.sqlite3').exists():return []
    zone=config.get('calendar',{}).get('timezone','America/Los_Angeles')
    day=now.astimezone(ZoneInfo(zone)).date().isoformat()
    store=Tasks(home,owner);cursor='';items=[]
    while len(items)<100:
        page=store.search(status='active',cursor=cursor)
        for t in page['tasks']:
            # An explicitly timed reminder owns its alert; summaries may still
            # mention the commitment without replacing the requested reminder.
            if heartbeat and t['remind_at']:continue
            due=datetime.fromisoformat(t['due_at']) if t['due_at'] else None
            if due and due>now+timedelta(days=horizon_days):continue
            if not due and t['status']!='waiting' and t['priority']!='high':continue
            key=notice_id(t)
            status='Waiting on '+t['waiting_on'] if t['status']=='waiting' else 'High priority' if not due else 'Due '+due.astimezone(ZoneInfo(zone)).strftime('%b %d, %-I:%M %p')
            items.append({'id':key,'kind':'personal_task','title':t['title'],'status':status,'url':'',
                          'task_id':t['id'],'due_at':t['due_at'],'priority':t['priority'],
                          'notice_day':day,'dependencies':t['dependencies']})
        cursor=page['cursor']
        if not cursor:break
    items.sort(key=lambda t:(t['due_at'] or '9999',t['priority']!='high',t['task_id']))
    return items[:100]


def prior_notices(home,config,exclude_key):
    """Sending receipts reserve their notices until reconciliation completes."""
    seen=set()
    for rel in ('digest/digest.sqlite3','heartbeat/digest/digest.sqlite3'):
        path=Path(home)/rel
        if not path.exists():continue
        db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=10)
        try:
            rows=db.execute("SELECT key,data FROM runs WHERE scope=? AND status IN ('sent','sending')",(scope(config),))
            for key,data in rows:
                if key==exclude_key:continue
                run=json.loads(data)
                for notice in run.get('payload',{}).get('task_notices',[]):
                    seen.add((notice['id'],notice['day']))
        finally:db.close()
    return seen


def filter_notices(manager,run,now):
    """Called under the shared delivery lock, before the sending receipt is saved."""
    if run['status']!='ready':return True
    notices=run['payload'].get('task_notices',[])
    if not notices:return True
    seen=prior_notices(manager.service.store.home,manager.service.config,run['key'])
    duplicates=[n for n in notices if (n['id'],n['day']) in seen]
    tasks=Tasks(manager.service.store.home,owner_key(manager.service.config))
    for notice in notices:
        if not notice.get('task_id') or notice in duplicates:continue
        try:task=tasks.get(notice['task_id'])
        except ValueError:duplicates.append(notice);continue
        if task['status'] not in ('open','waiting') or notice_id(task)!=notice['id']:duplicates.append(notice)
    if not duplicates:return True
    remove={n['line'] for n in duplicates};ids={n['id'] for n in duplicates}
    payload=run['payload']
    payload['text']='\n'.join(line for line in payload['text'].splitlines() if line not in remove)
    payload['task_notices']=[n for n in notices if n not in duplicates]
    payload['news']=[n for n in payload.get('news',[]) if n['id'] not in ids]
    # An hourly check may have nothing left; a morning digest retains its outlook.
    if payload['text'].strip()=='Needs your attention:':run['status']='quiet'
    manager.db.save(run,now)
    return run['status']=='ready'
