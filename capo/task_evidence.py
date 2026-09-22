"""Refresh linked task sources before treating saved commitments as current."""
import re
import time
from datetime import datetime, timezone
from urllib.parse import quote

from .contracts import TEXTS, object_schema
from .research_tools import ReadTool
from .tasks import Tasks

GUIDANCE = ('Saved task status and notes are historical claims, not proof that work remains. '
            'Inspect source_check before saying a reply is owed or a task is overdue. '
            'A later SENT message proves a reply was sent, not that every requested outcome was completed. '
            'Read the latest thread content when necessary, including newer incoming questions. '
            'For grouped commitments check every source. Missing, truncated, unsupported, or failed reads '
            'mean unverified; never turn that into an assertion of no reply. '
            'Retain owner corrections and paused/closed states. Reconcile clear completed outcomes with '
            'the existing task ID; do not create duplicate obligations or reopen closed work. ')


class TaskEvidence:
    def __init__(self, home, owner, config):
        self.tasks = Tasks(home, owner)
        self.config = config
        self.mail = None
        self.mail_reads = None
        with self.tasks.connection() as db:
            db.execute('CREATE TABLE IF NOT EXISTS task_source_checks(task_id TEXT PRIMARY KEY, checked_at TEXT)')

    def source(self, ref, threads):
        if not re.fullmatch(r'email:[a-fA-F0-9]{1,128}', ref):
            return {'source': ref, 'status': 'unsupported', 'coverage': 'Use the corresponding shared source tools to verify this reference.'}
        if not self.config.get('gmail', {}).get('enabled'):
            return {'source': ref, 'status': 'unavailable', 'coverage': 'Gmail is not enabled.'}
        from .gmail import Gmail
        if self.mail is None: self.mail = Gmail()
        message_id = ref.split(':', 1)[1]
        message = self.mail.get('messages/'+quote(message_id, safe=''), {'format':'metadata'})
        thread_id = message['threadId']
        if thread_id not in threads:
            threads[thread_id] = self.mail.get('threads/'+quote(thread_id, safe=''),
                {'format':'metadata', 'metadataHeaders':['From','To','Date','Subject']})
        thread = threads[thread_id]
        messages = sorted(thread.get('messages', []), key=lambda m: int(m.get('internalDate', '0')))
        if self.mail_reads is not None:
            self.mail_reads.known_threads.add(thread_id)
            self.mail_reads.known_ids.update(m['id'] for m in messages)
        after = [m for m in messages if int(m.get('internalDate','0')) > int(message.get('internalDate','0'))]
        sent = [m for m in after if 'SENT' in m.get('labelIds', []) and 'DRAFT' not in m.get('labelIds', [])]
        recent = []
        for m in messages[-8:]:
            recent.append({'id':m['id'], 'sent_by_owner':'SENT' in m.get('labelIds', []),
                'draft':'DRAFT' in m.get('labelIds', []), 'timestamp':m.get('internalDate'),
                'headers':{h['name'].lower():h['value'][:300] for h in m.get('payload',{}).get('headers',[])
                           if h['name'].lower() in ('from','to','subject','date')},
                'snippet':m.get('snippet','')[:700]})
        return {'source':ref, 'status':'checked', 'thread_id':thread_id,
                'url':'https://mail.google.com/mail/u/0/#all/'+thread_id,
                'later_sent_ids':[m['id'] for m in sent], 'messages':recent,
                'truncated':len(messages)>8, 'coverage':'Fresh linked thread; last eight message snippets, not full bodies. '
                'No later sent message here does not rule out a reply sent in a different thread or account.'}

    def refresh(self, ids):
        if len(ids)>20 or len(set(ids))!=len(ids): raise ValueError('Choose up to 20 unique task IDs')
        records=[]; threads={}; sources={}; attempted=0; started=time.monotonic()
        for id in ids:
            task=self.tasks.get(id); checks=[]
            for ref in task['sources']:
                if ref in sources: checks.append(sources[ref]); continue
                if attempted>=20 or time.monotonic()-started>30:
                    checks.append({'source':ref,'status':'not_checked','coverage':'Refresh budget reached; use tasks.refresh for the remaining tasks.'})
                    continue
                attempted+=1
                try: value=self.source(ref,threads)
                except Exception as exc:
                    from .health import diagnosis
                    category, action=diagnosis(exc)
                    value={'source':ref,'status':'unavailable','reason':category,'next_action':action}
                sources[ref]=value;checks.append(value)
            checked=bool(checks) and all(c['status']=='checked' for c in checks)
            record={'task_id':id,'revision':task['revision'],'checked_at':datetime.now(timezone.utc).isoformat(),
                    'status':'checked' if checked else 'unverified', 'sources':checks,
                    'coverage':'Source freshness only; task completion requires interpreting evidence. No task status changed.'}
            records.append(record)
            if checks and not any(c['status']=='not_checked' for c in checks):
                with self.tasks.connection() as db:
                    db.execute('INSERT OR REPLACE INTO task_source_checks VALUES (?,?)',(id,record['checked_at']))
        return {'tasks':records,'coverage':GUIDANCE}

    def overview(self):
        result=self.tasks.overview()
        tasks=[t for group in result['groups'].values() for t in group]
        records={r['task_id']:r for r in self.refresh([t['id'] for t in tasks[:20]])['tasks']}
        for task in tasks:
            task['source_check']=records.get(task['id'],{'status':'not_checked','coverage':'Use tasks.refresh to check this item.'})
        result['counts_are_saved_state'] = True
        result['coverage'] += ' Groups describe saved status, not verified current obligations. '+GUIDANCE
        return result

    def background(self):
        tasks=[]; cursor=''
        while len(tasks)<1000:
            page=self.tasks.search(cursor=cursor);tasks.extend(page['tasks']);cursor=page['cursor']
            if not cursor:break
        with self.tasks.connection() as db:
            last=dict(db.execute('SELECT task_id,checked_at FROM task_source_checks'))
        tasks=[t for t in tasks if any(s.startswith('email:') for s in t['sources'])]
        tasks.sort(key=lambda t:(last.get(t['id'],''), t['updated_at']))
        return self.refresh([t['id'] for t in tasks[:20]])

    @staticmethod
    def annotate(items, refreshed):
        checks={r['task_id']:r for r in refreshed['tasks']}
        for item in items:
            check=checks.get(item['task_id'],{'status':'not_checked'})
            item['source_check']=check
            if any(s.get('later_sent_ids') for s in check.get('sources',[])):
                item['status']='Later reply found; saved task needs reconciliation'
            else:
                item['status']='Saved task status, not confirmed current: '+item['status']

    def observations(self, refreshed):
        items=[]
        for task in refreshed['tasks']:
            for source in task['sources']:
                if source['status']!='checked':continue
                items.append({'kind':'email','id':source['source'], 'message_id':source['source'].split(':',1)[1],
                    'title':'Tracked task email thread updated', 'linked_task_id':task['task_id'],
                    'thread_evidence':source})
        return items

    def tools(self):
        return [ReadTool('tasks.overview', 'Review saved unfinished tasks with freshly checked linked email threads. Groups are saved statuses, not proof of current obligations. Inspect source_check; use source tools for full content or unsupported references.',object_schema({}),self.overview,fresh_for=300),
                ReadTool('tasks.refresh', 'Refresh current linked-source evidence for up to 20 task IDs before asserting work remains. Checks email threads directly, independent of recent inbox samples. Bounded snippets; no external writes or task status changes.',object_schema({'ids':TEXTS}),self.refresh,fresh_for=300)]
