"""Shared capability registry for the chief and specialists.

Adapters enforce access and limits. Providers see descriptions, not credentials.
"""
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .contracts import TEXT, object_schema
from .conversation import ConversationRouter, _write
from .research_tools import ReadTool, ReadTools, research
from .providers import Providers


class Documents:
    def __init__(self, home, owner):
        self.root=Path(home)/'documents'/hashlib.sha256(owner.encode()).hexdigest()
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.root.chmod(0o700)

    def list(self):
        items=[]
        for p in sorted(self.root.glob('*.json'),key=lambda x:x.stat().st_mtime,reverse=True)[:50]:
            v=json.loads(p.read_text())
            items.append({'id':p.stem,'title':v['title']})
        return {'documents':items,'coverage':'Up to 50 most recently saved documents.'}

    def read(self, id):
        if len(id)!=64 or any(c not in '0123456789abcdef' for c in id):
            raise ValueError('Invalid document ID')
        return json.loads((self.root/(id+'.json')).read_text())

    def save(self, receipt, result):
        if not result['document'].strip():return None
        key=hashlib.sha256(str(receipt).encode()).hexdigest()
        _write(self.root/(key+'.json'),{'title':result['document_title'],'content':result['document']})
        return key


def owner_key(config):
    return ':'.join(config.get(k,'local') for k in ('team_id','channel_id','owner_user_id'))


def shared_tools(home,config,documents):
    """Build a fresh request-scoped registry; do not initialize clients until called."""
    zone=config.get('calendar',{}).get('timezone','America/Los_Angeles')
    tools=[ReadTool('clock.now','Current local date, time and timezone.',object_schema({}),
                   lambda:{'now':datetime.now(ZoneInfo(zone)).isoformat(),'timezone':zone}),
           ReadTool('documents.list','List reusable private documents saved for this owner.',object_schema({}),documents.list),
           ReadTool('documents.read','Read a private document using an ID from documents.list.',object_schema({'id':TEXT}),documents.read)]
    from .tasks import Tasks
    tools.extend(Tasks(home, owner_key(config)).tools())
    if config.get('gmail',{}).get('enabled'):
        from .gmail import Gmail, GmailReadTools
        class LazyMail:
            client=None
            def get(self,*args,**kwargs):
                if self.client is None:self.client=Gmail()
                return self.client.get(*args,**kwargs)
        mail=GmailReadTools(LazyMail())
        tools.extend(mail.tools.values())
    if config.get('calendar',{}).get('enabled'):
        def events(start,end):
            from .calendar import GoogleCalendar,instant
            first,last=instant(start),instant(end)
            if not 0<(last-first).total_seconds()<=31*86400:raise ValueError('Calendar range must be at most 31 days')
            rows=GoogleCalendar().events(start,end)
            return {'events':[{k:e[k] for k in ('id','summary','start','end','location') if k in e} for e in rows],
                    'coverage':'Primary calendar only.'}
        tools.append(ReadTool('calendar.events','Read primary calendar events in an explicit RFC3339 start/end window, maximum 31 days.',
                              object_schema({'start':TEXT,'end':TEXT}),events))
    repositories=config.get('repositories',{})
    if repositories:
        def issues(repository):
            from .github import GitHub,remote_repository
            from .repository import git
            value=repositories[repository]
            name=remote_repository(git(value['path'],'remote','get-url','origin'))
            rows=GitHub(value.get('github_auth','default')).issues(name)
            return {'repository':repository,'issues':rows[:30],'coverage':'Up to 30 open issues; not a complete repository audit.'}
        tools.append(ReadTool('github.issues','Read open issues for a configured repository alias.',
            object_schema({'repository':{'type':'string','enum':list(repositories)}}),issues))
    return ReadTools(tools)


class CapabilityConversation(ConversationRouter):
    def __init__(self,home,config):
        self.home=Path(home);self.config=json.loads(json.dumps(config))
        self.documents=Documents(home,owner_key(config))
        super().__init__(home/'capabilities'/hashlib.sha256(owner_key(config).encode()).hexdigest())

    def _run(self,directory,context,schema,fd):
        try:
            result=research(Providers(timeout=90),shared_tools(self.home,self.config,self.documents),context,directory,
                instructions='You are Capo, chief of staff. Choose and combine tools to fulfill the request. '
                'For writing from owner examples, use their own sent prose, not received messages. '
                'Do not substitute an unrelated inbox summary. Discover useful saved documents when relevant. '
                'For a requested reusable document, return it for private storage. Never assume a connection '
                'is unavailable because an earlier bot message said so; the current tool catalog is authoritative.')
            key=self.documents.save(directory,result)
            _write(directory/'research.json',result)
            if key:_write(directory/'document.json',{'id':key,'title':result['document_title'],'content':result['document']})
            reply=result['reply']
        except Exception:
            reply='I couldn’t finish that request. The available evidence or tools were insufficient; please try again.'
        try:_write(directory/'outcome.json',{'route':{'action':'reply','repository':'','objective_id':'','reply':reply}})
        finally:os.close(fd)
