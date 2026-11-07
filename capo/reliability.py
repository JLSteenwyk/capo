"""Private, derived reliability evidence; never a second task/action ledger."""
import hashlib
import json
import re
import sqlite3
import time
from collections import Counter
from contextlib import closing
from pathlib import Path

from .contracts import TEXT,object_schema
from .research_tools import ReadTool
from .request_timing import RequestTiming,summary

DAYS={'type':'string','enum':['1','7','30','90']}
STATUSES={'reported_complete','complete','partial','failed','cancelled','unassessed','unknown'}


def read_json(path,default):
    if not path.exists() or path.stat().st_size>2_000_000:return default
    try:
        value=json.loads(path.read_text())
        return value if isinstance(value,type(default)) else default
    except (OSError,ValueError):return default


class Reliability:
    def __init__(self,home,config):
        from .capabilities import owner_key
        self.home=Path(home);self.config=config;self.owner=owner_key(config)
        self.root=self.home/'reliability'/hashlib.sha256(self.owner.encode()).hexdigest()
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700);self.root.chmod(0o700)
        self.path=self.root/'observations.sqlite3'
        with closing(sqlite3.connect(self.path,timeout=10)) as db,db:
            db.execute('CREATE TABLE IF NOT EXISTS observations(id TEXT PRIMARY KEY,received REAL,data TEXT NOT NULL)')
        self.path.chmod(0o600)

    def capture(self,event,status,result=None,thread='',category='',at=None):
        """Capture terminal metadata only. Raw prompts, replies and errors stay put."""
        at=time.time() if at is None else at;result=result if isinstance(result,dict) else {}
        timeline=RequestTiming(self.home,event).read()
        received=next((r['at'] for r in timeline if r['phase']=='received'),at)
        errors=Counter();uncertain=0
        for receipt in result.get('receipts',[]):
            name=receipt.get('tool','')
            if not re.fullmatch(r'[a-z][a-z0-9_.]{0,79}',name):name='unclassified'
            if 'error' in receipt:errors[name]+=1
            uncertain+=bool(receipt.get('uncertain'))
        outcomes=result.get('outcome_report',{}).get('outcomes',[])
        statuses=Counter(v.get('status') for v in outcomes)
        status=status if status in STATUSES else 'unknown'
        if category not in ('temporary','rate_limit','unavailable','authentication','permission_or_unconfirmed_action','failed'):category=''
        url=''
        if thread and re.fullmatch(r'\d+(?:\.\d+)?',str(thread)) and self.config.get('channel_id'):
            url='https://app.slack.com/archives/'+self.config['channel_id']+'/p'+str(thread).replace('.','')
        row={'id':RequestTiming(self.home,event).key,'status':status,'received_at':received,'captured_at':at,
             'tool_errors':dict(errors),'uncertain_receipts':uncertain,'category':category,
             'needs_input_items':statuses['needs_input'],'partial_items':statuses['partial'],'url':url,
             'evidence':'Host-recorded outcome and receipt metadata; semantic quality is not automatically graded.'}
        with closing(sqlite3.connect(self.path,timeout=10)) as db,db:
            db.execute('INSERT OR IGNORE INTO observations VALUES(?,?,?)',(row['id'],received,json.dumps(row)))
        return row

    def collect(self,days,now):
        """Backfill old delivery records and collect pending work without inventing outcomes."""
        if type(days) is not int or not 1<=days<=90:raise ValueError('Choose 1–90 days')
        since=now-days*86400;pending=[];scanned=0;truncated=False;gaps=0
        with closing(sqlite3.connect(self.path,timeout=10)) as db:
            captured={r[0] for r in db.execute('SELECT id FROM observations')}
        path=self.home/'capo.sqlite3'
        if path.exists():
            with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
                tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if 'slack_inbox' in tables:
                    rows=db.execute("SELECT id,data,handled FROM slack_inbox WHERE json_extract(data,'$.team_id')=? AND json_extract(data,'$.event.channel')=? AND json_extract(data,'$.event.user')=? ORDER BY rowid DESC LIMIT 5001",
                        tuple(self.config.get(k,'') for k in ('team_id','channel_id','owner_user_id'))).fetchall()
                    truncated=len(rows)>5000
                    for event,raw,handled in rows[:5000]:
                        scanned+=1;body=json.loads(raw);message=body['event']
                        try:at=float(message['ts'])
                        except (KeyError,TypeError,ValueError):gaps+=1;continue
                        timing=RequestTiming(self.home,event)
                        if timing.key in captured:continue
                        received=next((r['at'] for r in timing.read() if r['phase']=='received'),at)
                        if not since<=received<=now:continue
                        root=self.home/'capabilities'/hashlib.sha256(self.owner.encode()).hexdigest()/'conversation'/timing.key
                        if (root/'reliability-capture-error.json').exists():gaps+=1
                        outcome=read_json(root/'outcome.json',{})
                        result=read_json(root/'research.json',{})
                        delivery={}
                        if 'slack_deliveries' in tables:
                            saved=db.execute('SELECT data FROM slack_deliveries WHERE event_id=?',(event,)).fetchone()
                            if saved:delivery=json.loads(saved[0])
                        if not outcome and not delivery and not handled:
                            pending.append({'id':timing.key,'received_at':received,'status':'pending'})
                            continue
                        status=outcome.get('status') or ('failed' if delivery.get('failed') else delivery.get('outcome_status') or 'unknown')
                        if not result:result={'receipts':read_json(root/'receipts.json',[])}
                        self.capture(event,status,result,message.get('thread_ts',message.get('ts','')),at=at)
        with closing(sqlite3.connect(self.path,timeout=10)) as db:
            rows=[json.loads(r[0]) for r in db.execute('SELECT data FROM observations WHERE received>=? AND received<=? ORDER BY received DESC,id',(since,now))]
        known={r['id'] for r in rows};rows+= [r for r in pending if r['id'] not in known]
        return rows,{'scanned_owner_messages':scanned,'scan_limit':5000,'more_history_available':truncated,'source_gaps':gaps}

    def automation_evidence(self,days,now):
        from .digest import scope
        rows=[];limited=False
        if not all(k in self.config for k in ('team_id','channel_id','owner_user_id')):
            return rows,limited
        for kind,relative in (('digest','digest/digest.sqlite3'),('hourly','heartbeat/digest/digest.sqlite3'),('scheduled','scheduled/digest/digest.sqlite3')):
            path=self.home/relative
            if not path.exists():continue
            with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
                saved=db.execute('SELECT key,data,updated FROM runs WHERE scope=? AND updated>=? ORDER BY updated DESC LIMIT 1001',
                                 (scope(self.config),now-days*86400)).fetchall()
            limited=limited or len(saved)>1000
            for key,raw,updated in saved[:1000]:
                run=json.loads(raw);created=run.get('created',updated)
                if run.get('preview') or not now-days*86400<=created<=now:continue
                status=run.get('status','unknown')
                if status not in ('sent','quiet','failed','expired','cancelled','queued','building','ready','sending','delivery_unknown'):status='unknown'
                url=''
                ts=run.get('thread_ts') or run.get('ts','')
                if re.fullmatch(r'\d+(?:\.\d+)?',str(ts)):
                    url='https://app.slack.com/archives/'+self.config['channel_id']+'/p'+str(ts).replace('.','')
                rows.append({'id':hashlib.sha256(key.encode()).hexdigest(),'kind':'automation','source':kind,
                             'received_at':created,'status':status,'url':url,'source_id':key,
                             'window_elapsed':status in ('queued','building','ready','sending','delivery_unknown') and type(run.get('deadline')) in (int,float) and run['deadline']<=now,
                             'verification_incomplete':bool(run.get('verification_incomplete'))})
        return sorted(rows,key=lambda r:(r['received_at'],r['id']),reverse=True),limited

    def report(self,days=7,now=None):
        now=time.time() if now is None else now
        rows,coverage=self.collect(days,now);counts=Counter(r['status'] for r in rows)
        incidents=[r for r in rows if r['status'] in ('failed','partial') or r.get('tool_errors') or r.get('uncertain_receipts')]
        groups=Counter()
        for r in incidents:
            for tool in r.get('tool_errors',{}):groups['tool error: '+tool]+=1
            if r['status'] in ('failed','partial'):groups[r.get('category') or r['status']]+=1
            if r.get('uncertain_receipts'):groups['uncertain action evidence']+=1
        metrics=summary(self.home,days,now,request_keys={r['id'] for r in rows})
        automation,limited=self.automation_evidence(days,now)
        automation_issues=[r for r in automation if r['status'] in ('failed','expired','delivery_unknown') or r['verification_incomplete'] or r['window_elapsed']]
        automation_counts=Counter(r['status'] for r in automation)
        for r in automation_issues:groups[r['source']+' check: '+r['status']]+=1
        value={'days':days,'as_of':now,'requests':len(rows),'outcomes':dict(counts),'incident_requests':len(incidents),
               'needs_input_requests':sum(bool(r.get('needs_input_items')) for r in rows),
               'review_signals':[{'signal':k,'observations':v} for k,v in groups.most_common(5)],
               'recurring_signals':[{'signal':k,'observations':v} for k,v in groups.most_common() if v>=2][:5],
               'automation_runs':{'count':len(automation),'outcomes':dict(automation_counts),'incident_runs':len(automation_issues),'more_history_available':limited,'examples':automation_issues[:3]},
               'latency':metrics,'examples':[{'id':r['id'],'status':r['status'],'url':r.get('url','')} for r in incidents[:3]],
               'coverage':coverage,'unnecessary_clarifications':None,'semantic_success_rate':None,
               'limits':'Reported completion is not an independent quality grade. Needs-input is not automatically an unnecessary question. Missing/unreadable/legacy outcomes stay unknown. First-response timing can include a progress reply; end-to-end timing requires final delivery. Pending requests are not declared failed. Capture stores only derived metadata; existing private sources remain authoritative.'}
        value['text']=render(value)
        # Durable private weekly evidence, reusable by reflection without transcripts.
        from .conversation import _write
        _write(self.root/'latest-scorecard.json',value)
        return value

    def incidents(self,days,cursor):
        if cursor and (not cursor.isdigit() or len(cursor)>8):raise ValueError('Invalid incident cursor')
        rows,coverage=self.collect(days,time.time())
        rows=[r for r in rows if r['status'] in ('failed','partial') or r.get('tool_errors') or r.get('uncertain_receipts')]
        automation,limited=self.automation_evidence(days,time.time())
        rows += [r for r in automation if r['status'] in ('failed','expired','delivery_unknown') or r['verification_incomplete'] or r['window_elapsed']]
        rows.sort(key=lambda r:(r['received_at'],r['id']),reverse=True)
        coverage['automation_scan_limited']=limited
        start=int(cursor or '0');page=rows[start:start+25]
        return {'incidents':page,'cursor':str(start+len(page)) if start+len(page)<len(rows) else '',
                'coverage':coverage,'guidance':'Use source_id with team.status for automation records; use conversation links/experience for requests. Inspect private source receipts before proposing a fix. Group recurring signals into synthetic regression ideas. No public search, code change or action replay is authorized by this evidence.'}

    def tools(self):
        return [ReadTool('reliability.report','Build a private evidence-based reliability scorecard for 1, 7, 30 or 90 days from owner outcomes, delivery records and lifecycle timing. Includes linked failure examples, repeated signals and explicit measurement gaps. Does not replay actions or grade semantic correctness. Use for recurring reviews and on-demand troubleshooting.',object_schema({'days':DAYS}),lambda days:self.report(int(days)),fresh_for=300),
                ReadTool('reliability.incidents','Page private failure/partial/tool-error metadata for reflection. Empty cursor starts. Sources are references, not permission to repeat actions or publish personal data. Propose synthetic regression cases only after inspecting actual source evidence.',object_schema({'days':DAYS,'cursor':TEXT}),lambda days,cursor:self.incidents(int(days),cursor),fresh_for=300)]


def render(value):
    c=value['outcomes'];lines=[f"Capo reliability — last {value['days']} days ({value['requests']} requests)",
        f"Reported complete: {c.get('reported_complete',0)+c.get('complete',0)} · Partial: {c.get('partial',0)} · Failed: {c.get('failed',0)} · Pending: {c.get('pending',0)} · Other/unassessed: {sum(v for k,v in c.items() if k not in ('reported_complete','complete','partial','failed','pending'))}"]
    for name,label in [('first_response','First response'),('execution','Execution'),('end_to_end','End-to-end delivery')]:
        m=value['latency']['metrics'].get(name)
        if m:lines.append(f"{label}: median {m['median_seconds']:g}s; p90 {m['p90_seconds']:g}s (n={m['count']}).")
    if not value['latency']['metrics']:lines.append('No complete timing samples yet.')
    lines.append(f"{value['incident_requests']} requests have failure/partial/tool-error signals; {value['needs_input_requests']} needed input.")
    auto=value['automation_runs'];c=auto['outcomes']
    lines.append(f"Automation runs: {auto['count']}; needing review: {auto['incident_runs']}; failed/expired: {c.get('failed',0)+c.get('expired',0)}; unconfirmed posts: {c.get('delivery_unknown',0)}.")
    for group in value['review_signals'][:3]:lines.append(f"• {group['signal']}: {group['observations']} observations.")
    for row in value['examples']:
        if row['url']:lines.append('Review '+row['status']+': '+row['url'])
    for row in auto['examples']:
        if row['url']:lines.append('Review '+row['source']+' '+row['status']+': '+row['url'])
    lines.append('Quality and unnecessary questions are unassessed; reported completion is not a quality score.')
    if value['coverage']['more_history_available'] or auto['more_history_available']:lines.append('History scan limit reached; this is partial coverage.')
    return '\n'.join(lines)


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
                    cancelled=(directory/'cancelled.json').exists(),result=checkpoint.get('result'),failed=bool(outcome.get('error') or outcome.get('status')=='failed'))
                status=classify(record,now.timestamp())
                if not status:continue
                items.append({'id':'request-health:'+key,'kind':'connection','title':'A saved request needs inspection',
                    'status':status,'repeated_failures':sum(bool(r.get('error')) for r in checkpoint.get('receipts',[]))>=3,
                    'url':'https://app.slack.com/archives/'+event['channel']+'/p'+thread.replace('.',''),
                    'started_at':started,
                    'next_action':'Inspect saved results and current service state. Do not repeat unconfirmed actions.'})
            except (ValueError,KeyError,TypeError,AttributeError,OSError):
                if not owned:continue
                items.append({'id':'request-health:'+hashlib.sha256(str(event_id).encode()).hexdigest(),
                    'kind':'connection','title':'A saved request could not be inspected','status':'unreadable',
                    'next_action':'Inspect the private request receipt; do not replay it.'})
    return {'items':items[:20],'coverage':'Latest 500 request rows, up to seven days, at most 20 concerns. Pending after 15 minutes means inspect, not proven stuck. Newer owner turns supersede older completed replies; waiting for owner input is not a failure.',
            'scanned':scanned,'truncated':len(rows)==500 or len(items)>20}
