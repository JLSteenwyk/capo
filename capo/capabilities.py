"""Shared capability registry for the chief and specialists.

Adapters enforce access and limits. Providers see descriptions, not credentials.
"""
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .contracts import TEXT, TEXTS, object_schema
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


def shared_tools(home,config,documents,request=None):
    """Build a fresh request-scoped registry; do not initialize clients until called."""
    zone=config.get('calendar',{}).get('timezone','America/Los_Angeles')
    tools=[ReadTool('clock.now','Current local date, time and timezone.',object_schema({}),
                   lambda:{'now':datetime.now(ZoneInfo(zone)).isoformat(),'timezone':zone}),
           ReadTool('documents.list','List reusable private documents saved for this owner.',object_schema({}),documents.list),
           ReadTool('documents.read','Read a private document using an ID from documents.list.',object_schema({'id':TEXT}),documents.read)]
    from .web_tools import WebTools
    tools.extend(WebTools(home).tools())
    if request and request.get("request_thread"):
        from .request_memory import RequestMemory
        tools.extend(RequestMemory(home,owner_key(config),request["request_thread"]).tools(request.get("request_event", "local")))
    from .date_tools import tools as date_tools
    tools.extend(date_tools())
    from .tasks import Tasks
    tools.extend(Tasks(home, owner_key(config)).tools())
    from .schedules import Schedules
    tools.extend(Schedules(home, owner_key(config)).tools())
    if config.get('gmail',{}).get('enabled'):
        from .gmail import Gmail, GmailReadTools
        class LazyMail:
            client=None
            def get(self,*args,**kwargs):
                if self.client is None:self.client=Gmail()
                return self.client.get(*args,**kwargs)
        mail=GmailReadTools(LazyMail())
        tools.extend(mail.tools.values())
        if config.get('gmail',{}).get('drafts',False):
            from .drafts import DraftTools
            class LazyDraftMail:
                client=None
                def ready(self):
                    if self.client is None:self.client=Gmail(drafts=True)
                    return self.client
                def get(self,*args,**kwargs):return self.ready().get(*args,**kwargs)
                def draft_write(self,*args,**kwargs):return self.ready().draft_write(*args,**kwargs)
                def draft_exists(self,*args,**kwargs):return self.ready().draft_exists(*args,**kwargs)
            tools.extend(DraftTools(LazyDraftMail(),mail,home,owner_key(config),request).tools())
    if config.get('calendar',{}).get('enabled'):
        calendar_cache={}
        def events(start,end):
            from .calendar import GoogleCalendar,instant
            first,last=instant(start),instant(end)
            if not 0<(last-first).total_seconds()<=31*86400:raise ValueError('Calendar range must be at most 31 days')
            rows=GoogleCalendar().events(start,end)
            calendar_cache.update({e['id']:e for e in rows if isinstance(e.get('id'),str) and e['id']})
            normalized=[]
            for e in rows:
                value={k:e[k] for k in ('id','summary','start','end','location','transparency','status') if k in e}
                for key in ('start','end'):
                    if value.get(key,{}).get('dateTime'):
                        local=instant(value[key]['dateTime']).astimezone(ZoneInfo(zone))
                        value[key]={'dateTime':local.isoformat(),'timeZone':zone}
                normalized.append(value)
            return {'events':normalized,
                    'coverage':'Primary calendar only.'}
        tools.append(ReadTool('calendar.events','Read primary calendar events in an explicit RFC3339 start/end window, maximum 31 days.',
                              object_schema({'start':TEXT,'end':TEXT}),events))
        def free_time(start,end,work_start,work_end,weekdays,minimum_minutes):
            from .availability import availability
            return availability(events(start,end)['events'],start,end,zone,work_start,work_end,weekdays,minimum_minutes)
        tools.append(ReadTool('calendar.availability','Find free windows and event conflicts within explicit work hours. Weekdays: 0 Monday through 6 Sunday. Supply owner preferences or label assumptions. Read-only; never books time.',
            object_schema({'start':TEXT,'end':TEXT,'work_start':TEXT,'work_end':TEXT,'weekdays':TEXTS,'minimum_minutes':TEXT}),free_time))
        def change(action,event_id,title,location,start,end,all_day,operation_id):
            from .calendar import GoogleCalendar,apply,event_body,writable
            from .effects import Effects
            plan=dict(action=action,event_id=event_id,title=title,location=location,start=start,end=end,all_day=all_day,reply='')
            effects=Effects(home,owner_key(config));request={'kind':'calendar-change','plan':plan}
            previous=effects.completed(operation_id,request)
            if previous is not None:return previous
            if action in ('create','update'):event_body(plan,zone)
            if action in ('update','delete') and (event_id not in calendar_cache or not writable(calendar_cache[event_id])):
                raise ValueError('Read the personal event first; guests and recurring events cannot be changed')
            client=GoogleCalendar()
            directory=Path(home)/'calendar-tools'/hashlib.sha256(operation_id.encode()).hexdigest()
            directory.mkdir(parents=True,exist_ok=True,mode=0o700)
            def execute():
                reply=apply(client,plan,list(calendar_cache.values()),zone,directory)
                target=hashlib.sha256(str(directory).encode()).hexdigest() if action=='create' else event_id
                return {'changed':True,'event_id':target,'reply':reply}
            return effects.run(operation_id,request,execute)
        tools.append(ReadTool('calendar.change','Apply an explicitly owner-requested personal calendar change, never a planning suggestion. Reuses the calendar permission and If-Match checks. Read events first before update/delete; no guests or recurring events. Provide full preserved title/location/times on update. Timed values require explicit local offsets; all-day end date is exclusive. Unused strings empty. Never retry an unconfirmed change blindly.',
            object_schema({'action':{'type':'string','enum':['create','update','delete']},'event_id':TEXT,'title':TEXT,'location':TEXT,'start':TEXT,'end':TEXT,'all_day':{'type':'boolean'}}),change,mutates=True))
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
            result=research(Providers(timeout=90),shared_tools(self.home,self.config,self.documents,context),context,directory,
                instructions='You are Capo, chief of staff. Choose and combine tools to fulfill the request. '
                'For writing from owner examples, use their own sent prose, not received messages. '
                'Do not substitute an unrelated inbox summary. Discover useful saved documents when relevant. '
                'For a requested reusable document, return it for private storage. Never assume a connection '
                'is unavailable because an earlier bot message said so; the current tool catalog is authoritative. '
                'For daily or weekly planning, combine active tasks, deadlines, dependencies, waiting items, calendar availability and relevant email evidence. '
                'Highlight conflicts, preparation and work windows; label assumptions about work hours and task duration. '
                'Suggestions are not completed actions. Only change a task or schedule when the owner requested it; read its latest revision first.',max_calls=10)
            key=self.documents.save(directory,result)
            _write(directory/'research.json',result)
            if key:_write(directory/'document.json',{'id':key,'title':result['document_title'],'content':result['document']})
            reply=result['reply']
        except Exception as exc:
            from .providers import AuthenticationError
            if isinstance(exc, AuthenticationError):
                reply='Claude’s login needs to be renewed on the computer running Capo. Your request and any saved progress are preserved.'
            else:
                receipts_path=directory/'receipts.json'
                receipts=json.loads(receipts_path.read_text()) if receipts_path.exists() else []
                failed=[r['tool'] for r in receipts if 'error' in r]
                detail=('The '+failed[-1]+' tool did not return usable evidence.' if failed else
                        'The reasoning step stopped before I could finish.')
                reply=detail+' Your request and any action receipts are saved; I have not confirmed completion.'
        try:
            if context.get('request_thread'):
                from .request_memory import RequestMemory
                RequestMemory(self.home,owner_key(self.config),context['request_thread']).record(
                    context.get('request_event',str(directory)), 'outcome', {'reply':reply})
            _write(directory/'outcome.json',{'route':{'action':'reply','repository':'','objective_id':'','reply':reply}})
        finally:os.close(fd)
