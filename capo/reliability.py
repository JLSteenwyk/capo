"""Bounded, read-only inspection of durable request outcomes, independent of intent."""
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path


def classify(record, now, grace=900):
    """Return a concern, not a claim that a worker died or permission to replay it."""
    if record.get('cancelled'):return None
    if record.get('pending'):
        if now-record['started_at']<grace:return None
        return 'delivery_pending' if record.get('reply_ready') else 'request_pending'
    result=record.get('result') or {}
    if result.get('status')=='partial':
        unfinished=[r for r in result.get('outcome_report',{}).get('outcomes',[]) if r.get('status')!='complete']
        if unfinished and all(r.get('status')=='needs_input' for r in unfinished):return None
        return 'incomplete_outcome'
    if record.get('failed'):return 'request_failed'
    return None


def review(home, config, now):
    from .capabilities import owner_key
    if not all(config.get(k) for k in ('team_id','channel_id','owner_user_id')):
        return {'items':[],'coverage':'Owner scope is not configured.'}
    home=Path(home);path=home/'capo.sqlite3'
    if not path.exists():return {'items':[],'coverage':'No saved request ledger.'}
    root=home/'capabilities'/hashlib.sha256(owner_key(config).encode()).hexdigest()/'conversation'
    items=[];threads=set();scanned=0
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'slack_inbox' not in tables:return {'items':[],'coverage':'No saved request ledger.'}
        rows=db.execute('SELECT id,data,handled FROM slack_inbox ORDER BY rowid DESC LIMIT 500').fetchall()
        for event_id,data,handled in rows:
            scanned+=1
            owned=False
            try:
                body=json.loads(data);event=body['event']
                if (body.get('team_id')!=config.get('team_id') or event.get('channel')!=config.get('channel_id')
                        or event.get('user')!=config.get('owner_user_id')):continue
                owned=True
                thread=event.get('thread_ts',event['ts'])
                # A newer owner turn supersedes older completed replies in this thread,
                # but an old undelivered request still warrants inspection.
                superseded=thread in threads;threads.add(thread)
                if handled and superseded:continue
                started=float(event['ts'])
                if started<now.timestamp()-7*86400:continue
                key=hashlib.sha256(str(event_id).encode()).hexdigest();directory=root/key
                def read(name):
                    p=directory/name
                    return json.loads(p.read_text()) if p.exists() else {}
                checkpoint=read('checkpoint.json');outcome=read('outcome.json')
                ready='slack_deliveries' in tables and db.execute('SELECT 1 FROM slack_deliveries WHERE event_id=?',(event_id,)).fetchone() is not None
                record=dict(pending=not handled,reply_ready=ready,started_at=started,
                    cancelled=(directory/'cancelled.json').exists(),result=checkpoint.get('result'),failed=bool(outcome.get('error')))
                status=classify(record,now.timestamp())
                if not status:continue
                items.append({'id':'request-health:'+key,'kind':'connection','title':'A saved request needs inspection',
                    'status':status,'repeated_failures':sum(bool(r.get('error')) for r in checkpoint.get('receipts',[]))>=3,
                    'url':'https://app.slack.com/archives/'+event['channel']+'/p'+thread.replace('.',''),
                    'next_action':'Inspect saved results and current service state. Do not repeat unconfirmed actions.'})
            except (ValueError,KeyError,TypeError,AttributeError,OSError):
                if not owned:continue
                items.append({'id':'request-health:'+hashlib.sha256(str(event_id).encode()).hexdigest(),
                    'kind':'connection','title':'A saved request could not be inspected','status':'unreadable',
                    'next_action':'Inspect the private request receipt; do not replay it.'})
    return {'items':items[:20],'coverage':'Latest 500 request rows, up to seven days, at most 20 concerns. Pending after 15 minutes means inspect, not proven stuck. Newer owner turns supersede older completed replies; waiting for owner input is not a failure.',
            'scanned':scanned,'truncated':len(rows)==500 or len(items)>20}
