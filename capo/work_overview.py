"""Unified read-only work projection; existing ledgers remain authoritative."""
import hashlib
import json
import sqlite3
from pathlib import Path
from .contracts import object_schema
from .research_tools import ReadTool


class WorkOverview:
    def __init__(self,home,config,task_evidence=None):
        self.home=Path(home);self.config=config;self.task_evidence=task_evidence
        from .capabilities import owner_key
        self.owner=owner_key(config)

    def read(self,refresh=True):
        from .tasks import Tasks
        from .schedules import Schedules
        from .effects import Effects
        groups={k:[] for k in ('active','scheduled','waiting_for_owner','blocked','completed','cancelled','uncertain','tracked')}
        store=Tasks(self.home,self.owner);cursor='';tasks=[]
        for _ in range(6):
            page=store.search(status='all',cursor=cursor);tasks.extend(page['tasks']);cursor=page['cursor']
            if not cursor:break
        checks={}
        if refresh and self.task_evidence:
            ids=[t['id'] for t in tasks if t['status'] in ('open','waiting')][:20]
            if ids:checks={r['task_id']:r for r in self.task_evidence.refresh(ids)['tasks']}
        for task in tasks:
            key=('completed' if task['status']=='completed' else 'cancelled' if task['status'] in ('cancelled','dismissed')
                 else 'blocked' if task.get('blocked_by') or task['status']=='paused' else 'tracked')
            details=task.get('follow_through',{})
            groups[key].append({'kind':'task','id':task['id'],'title':task['title'],'saved_status':task['status'],
                'next_step':details.get('next_action') or ('Inspect current sources before treating this as outstanding.' if key=='tracked' else ''),
                'sources':task.get('sources',[]),'waiting_on':task.get('waiting_on',''),'blocked_by':task.get('blocked_by',[]),'url':details.get('original_conversation',''),
                'source_check':checks.get(task['id'],{'status':'not_checked'}),
                'completion_evidence':details.get('completion_evidence',[])})
        for s in Schedules(self.home,self.owner).list()['schedules']:
            if s['enabled']:groups['scheduled'].append({'kind':'schedule','id':s['id'],'title':s['title'],
                'time':s['time'],'timezone':s['timezone'],'next_step':'Runs under its saved schedule; not a completed check.'})
        path=self.home/'capo.sqlite3'
        if path.exists():
            db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)
            try:
                tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if 'slack_inbox' in tables:
                    roots=self.home/'capabilities'/hashlib.sha256(self.owner.encode()).hexdigest()/'conversation'
                    threads_seen=set()
                    for eid,data,handled in db.execute('SELECT id,data,handled FROM slack_inbox ORDER BY rowid DESC LIMIT 100'):
                        body=json.loads(data);e=body.get('event',{})
                        if body.get('team_id')!=self.config.get('team_id') or e.get('channel')!=self.config.get('channel_id') or e.get('user')!=self.config.get('owner_user_id'):continue
                        thread=e.get('thread_ts',e['ts'])
                        superseded=thread in threads_seen
                        threads_seen.add(thread)
                        directory=roots/hashlib.sha256(str(eid).encode()).hexdigest()
                        def read(name):
                            p=directory/name;return json.loads(p.read_text()) if p.exists() else {}
                        result=read('checkpoint.json').get('result') or {};outcome=read('outcome.json')
                        status=outcome.get('status') or result.get('status','unknown')
                        items=result.get('outcome_report',{}).get('outcomes',[])
                        key=('tracked' if handled and superseded else 'cancelled' if (directory/'cancelled.json').exists() else 'active' if not handled else
                             'waiting_for_owner' if any(i['status']=='needs_input' for i in items) else
                             'blocked' if status in ('failed','partial') else 'completed' if status in ('reported_complete','complete') else 'tracked')
                        groups[key].append({'kind':'request','id':hashlib.sha256(str(eid).encode()).hexdigest(),
                            'title':e.get('text','')[:180],'request_status':status,'reply_delivered':bool(handled),
                            'superseded_by_later_turn':superseded,'outcomes':items,'completion_kind':'request_report',
                            'url':'https://app.slack.com/archives/'+e['channel']+'/p'+e.get('thread_ts',e['ts']).replace('.',''),
                            'next_step':' '.join(i.get('next_step','') for i in items if i.get('status')!='complete')[:500],
                            'coverage':'Reported outcome is receipt-validated, not a fresh recheck of external state.'})
                if 'objectives' in tables:
                    for (data,) in db.execute('SELECT data FROM objectives ORDER BY updated DESC LIMIT 50'):
                        o=json.loads(data);origin=o.get('slack',{})
                        if any(origin.get(k)!=self.config.get(k) for k in ('team_id','channel_id','owner_user_id')):continue
                        status=o['status'];key='completed' if status=='completed' else 'cancelled' if status=='cancelled' else 'waiting_for_owner' if status in ('awaiting_input','awaiting_approval') else 'blocked' if status=='blocked' else 'active'
                        groups[key].append({'kind':'development','id':o['id'],'title':o['request'][:180],
                            'saved_status':status,'next_step':o.get('question') or o.get('error',''),
                            'url':o.get('publication',{}).get('pr',{}).get('url',''),
                            'coverage':'Candidate acceptance and publication/merge are distinct stages.'})
            finally:db.close()
        for op,req in Effects(self.home,self.owner).pending(''):
            groups['uncertain'].append({'kind':'action','id':hashlib.sha256(op.encode()).hexdigest(),
                'title':req.get('kind','External action'),'next_step':'Inspect pending receipts and reconcile actual state; never repeat blindly.'})
        counts={k:len(v) for k,v in groups.items()}
        return {'groups':{k:v[:25] for k,v in groups.items()},'counts_in_scanned_coverage':counts,
                'coverage':{'task_more_available':bool(cursor),'requests_limit':100,'development_limit':50,'per_group_limit':25,
                    'source_refresh_requested':refresh,'note':'Saved open tasks are tracked, not proof a response is still owed. Review source_check, current source tools and completion evidence. Delivered replies and queued handoffs do not prove external actions completed.'}}

    def briefing(self):
        value=self.read(refresh=False)
        return {'counts_in_scanned_coverage':value['counts_in_scanned_coverage'],
                'focus':{k:value['groups'][k][:3] for k in ('active','waiting_for_owner','blocked','uncertain')},
                'coverage':value['coverage']}

    def tool(self):
        return ReadTool('work.overview','Show active requests, schedules, owner questions, blockers, completed/cancelled work, uncertain actions and tracked tasks from authoritative ledgers. Fresh source checks cover up to 20 open tasks; inspect unchecked sources before claiming work is owed. Does not mutate task state or send notices.',object_schema({}),self.read,fresh_for=300)
