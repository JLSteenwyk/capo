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
    from .specialist_tools import SpecialistTools
    tools.extend(SpecialistTools(home, owner_key(config)).tools(writable=bool((request or {}).get('owner_request'))))
    if all(config.get(k) for k in ('team_id', 'channel_id', 'owner_user_id')):
        from .digest_tools import DigestTools
        tools.extend(DigestTools(home, config, request).tools(writable=bool((request or {}).get('owner_request'))))
    if request and request.get('owner_request') and all(config.get(k) for k in ('team_id', 'channel_id', 'owner_user_id')):
        from .workflow_tools import WorkflowTools
        tools.extend(WorkflowTools(home, config, request).tools())
    from .web_tools import WebTools
    tools.extend(WebTools(home).tools())
    if request and request.get("request_thread"):
        from .request_memory import RequestMemory
        tools.extend(RequestMemory(home,owner_key(config),request["request_thread"]).tools(request.get("request_event", "local")))
    from .date_tools import tools as date_tools
    tools.extend(date_tools())
    from .tasks import Tasks
    from .delegation import settings as autonomy_settings
    policy = autonomy_settings(config)
    tasks = Tasks(home, owner_key(config), origin=(request or {}).get('owner_request'),
                  allowed_actions=[])
    tools.extend(tasks.tools())
    if (request or {}).get('owner_request') and policy['enabled']:
        from .delegation import delegation_tool
        tools.append(delegation_tool(tasks, policy))
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
        from .calendar_tools import CalendarTools
        calendar_tools=CalendarTools(zone)
        tools.extend(calendar_tools.tools())
        events=calendar_tools.events
        def free_time(start,end,work_start,work_end,weekdays,minimum_minutes):
            from .availability import availability
            return availability(events(start,end)['events'],start,end,zone,work_start,work_end,weekdays,minimum_minutes)
        tools.append(ReadTool('calendar.availability','Find free windows and event conflicts within explicit work hours. Weekdays: 0 Monday through 6 Sunday. Supply owner preferences or label assumptions. Read-only; never books time.',
            object_schema({'start':TEXT,'end':TEXT,'work_start':TEXT,'work_end':TEXT,'weekdays':TEXTS,'minimum_minutes':TEXT}),free_time))
        tools.extend(calendar_tools.action_tools(home,owner_key(config)))
    repositories=config.get('repositories',{})
    if repositories:
        from .github_tools import GitHubTools
        tools.extend(GitHubTools(repositories).tools())
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
    # Short introduction plus the complete bounded document.
    reply_limit = 16000
    recoverable = True

    def __init__(self,home,config):
        self.home=Path(home);self.config=json.loads(json.dumps(config))
        self.documents=Documents(home,owner_key(config))
        super().__init__(home/'capabilities'/hashlib.sha256(owner_key(config).encode()).hexdigest())

    def _run(self,directory,context,schema,fd):
        try:
            result=research(Providers(timeout=90),shared_tools(self.home,self.config,self.documents,context),context,directory,
                instructions='You are Capo, chief of staff. Choose and combine tools to fulfill the request. '
                'For relevant specialist expertise and saved preferences, use specialists.list/read and apply them in this same loop. '
                'For explicitly requested repository work, use development tools; for browser control, use browser.start when available. '
                'A queued handoff is not completion. Preserve existing publication and browser approval requirements. '
                'Use digest.read for morning-digest context or settings, and digest.change only for explicit owner feedback or schedule changes. '
                'For writing from owner examples, use their own sent prose, not received messages. '
                'Do not substitute an unrelated inbox summary. Discover useful saved documents when relevant. '
                'For a requested reusable document, return it for private storage. Never assume a connection '
                'is unavailable because an earlier bot message said so; the current tool catalog is authoritative. '
                'For daily or weekly planning, combine active tasks, deadlines, dependencies, waiting items, calendar availability and relevant email evidence. '
                'Highlight conflicts, preparation and work windows; label assumptions about work hours and task duration. '
                'Suggestions are not completed actions. Only change a task or schedule when the owner requested it; read its latest revision first. '
                'For explicitly requested work that needs later follow-through, save its task and use tasks.delegate. '
                'A request merely to track something or remind the owner is not permission to execute that underlying activity; do not delegate it.',max_calls=10, recovery=self.config.get('recovery'))
            key=self.documents.save(directory,result)
            _write(directory/'research.json',result)
            if key:_write(directory/'document.json',{'id':key,'title':result['document_title'],'content':result['document']})
            reply=result['reply']
        except Exception as exc:
            from .recovery import RetryLater
            if isinstance(exc, RetryLater):
                _write(directory/'retry.json', {'retry_at': exc.retry_at})
                os.close(fd)
                return
            from .providers import AuthenticationError
            if isinstance(exc, (AuthenticationError, PermissionError)):
                from .recovery import failure_summary
                reply=failure_summary(exc)
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
