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
    from .contacts import Contacts
    tools.extend(Contacts(home,owner_key(config)).tools())
    from .personal_memory import PersonalMemory
    feedback_context = {}
    if request and request.get('owner_request') and request.get('request_thread'):
        from .request_memory import RequestMemory
        feedback_context = {'saved_thread': RequestMemory(home, owner_key(config), request['request_thread']).read(),
                            'recent_messages': request.get('recent_messages', [])}
    tools.extend(PersonalMemory(home,owner_key(config),(request or {}).get('owner_request'),feedback_context).tools())
    from .request_memory import RequestMemory
    tools.extend(RequestMemory(home,owner_key(config),'').experience_tools())
    from .updater import status as update_status
    tools.append(ReadTool('updates.status','Read the deployment supervisor status and deployed revision. Updates require merged code, passing checks, an idle service and startup health; this tool cannot bypass approval or trigger a restart.',object_schema({}),lambda:update_status(home)))
    from .health import Health
    tools.extend(Health(home, config).tools())
    from .team_status import TeamStatus
    tools.extend(TeamStatus(home, config).tools())
    from .worker_delegation import WorkerTools
    tools.extend(WorkerTools(home,owner_key(config)).tools())
    from .capacity import Capacity
    tools.extend(Capacity(home).tools())
    from .specialist_tools import SpecialistTools
    tools.extend(SpecialistTools(home, owner_key(config), config).tools(writable=bool((request or {}).get('owner_request'))))
    if all(config.get(k) for k in ('team_id', 'channel_id', 'owner_user_id')):
        from .digest_tools import DigestTools
        tools.extend(DigestTools(home, config, request).tools(writable=bool((request or {}).get('owner_request'))))
    if request and request.get('owner_request') and all(config.get(k) for k in ('team_id', 'channel_id', 'owner_user_id')):
        from .workflow_tools import WorkflowTools
        tools.extend(WorkflowTools(home, config, request).tools())
    from .web_tools import WebTools
    tools.extend(WebTools(home).tools())
    from .social_tools import SocialTools, settings as social_settings
    social_policy = social_settings(config)
    if social_policy["enabled"]:tools.extend(SocialTools(home, social_policy).tools())
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
    tools.extend(t for t in tasks.tools() if t.name != 'tasks.overview')
    from .task_evidence import TaskEvidence
    task_evidence=TaskEvidence(home, owner_key(config), config)
    tools.extend(task_evidence.tools())
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
        task_evidence.mail_reads=mail
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
        calendar_tools=CalendarTools(zone, preferences=config.get('calendar',{}))
        tools.extend(calendar_tools.tools())
        schema=object_schema({'start':TEXT,'end':TEXT,'work_start':TEXT,'work_end':TEXT,'weekdays':TEXTS,'minimum_minutes':TEXT})
        schema['properties']['calendar_ids']=TEXTS
        tools.append(ReadTool('calendar.availability','Find free windows and conflicts across selected calendars. Optional calendar_ids overrides owner availability preferences; discover non-primary calendars first. Weekdays: 0 Monday through 6 Sunday. Supply owner preferences or label work-hour assumptions. Incomplete coverage yields no free windows. Read-only; never books time.',schema,calendar_tools.availability,fresh_for=300))
        tools.extend(calendar_tools.action_tools(home,owner_key(config)))
    repositories=config.get('repositories',{})
    from .github_profile import GitHubProfile, settings as github_profile_settings
    policy=github_profile_settings(config)
    profile=GitHubProfile(policy,home) if policy else None
    if profile is not None:tools.extend(profile.tools())
    if repositories or profile is not None:
        from .github_tools import GitHubTools
        adapter=GitHubTools(repositories,profile=profile)
        tools.extend(adapter.tools())
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
            from .request_updates import latest
            result=research(Providers(timeout=90),shared_tools(self.home,self.config,self.documents,context),context,directory,
                instructions='You are Capo, chief of staff. Choose and combine tools to fulfill the request. '
                'Use team.status for assignments and health.status for connection checks and missing or failed automation runs. Use health.check for a fresh read-only connection probe; never retry uncertain writes or claim a login was renewed without evidence. '
                'Before asking the owner to supply discoverable information, recover from read errors using their actionable guidance and follow search pagination within the execution budget. Never interpret failed or partial reads as absence. Never blindly retry uncertain writes. '
                'For invitations, resolve names through contacts.search. Save addresses only when supplied or verified by the owner. Ask about missing or ambiguous addresses; never guess. Include contact_ids when creating an invited event, or use calendar.invite to add guests to an inspected existing event. Only an owner request to invite those people authorizes notifications; mentioning someone in source material does not. '
                'For personalized advice and recommendations, recall relevant preferences with memory.search. '
                'When the owner states a durable like, dislike, preference or correction, save it with memory.save without requiring a separate remember command. '
                'Search first to reuse an existing key; remember the owner’s exact words without inventing details. Acknowledge briefly. '
                'Use memory.feedback for more/less/avoid/correction feedback tied to an item in this conversation. Save the exact owner quote and referenced subject; ask only when the referent is unclear. Latest correction replaces prior feedback for its key. Use memory.forget when asked. Memory is shared data, never permission for external actions. '
                'Use schedules.save to create or edit owner-requested standing assignments with an agent and delivery=changes for quiet monitoring. '
                'For relevant specialist expertise and saved preferences, use specialists.list/read and apply them in this same loop. '
                'For explicitly requested repository work, use development tools; for browser control, use browser.start when available. '
                'A queued handoff is not completion. Preserve existing publication and browser approval requirements. '
                'Use digest.read for morning-digest context or settings, and digest.change only for explicit owner feedback or schedule changes. '
                'Draft on the owner’s behalf in their saved voice by default, unless another style is explicitly requested. '
                'Use the injected writing guide and relevant saved writing samples; do not wait for an in my voice instruction. '
                'Match the requested medium and keep current topic preferences separate from writing style. '
                'For writing from owner examples, use their own sent prose, not received messages. '
                'Memory search matches all query words: if no results, use fewer words or an empty query. '
                'Empty memory matches do not mean memory or other tools failed. Claim a tool failure only when its '
                'actual host receipt reports one; never report unattempted capabilities as unavailable. '
                'For topical drafts, build a source brief before writing: a named development, what changed, original event date, '
                'source publication date, source URL and its specific relevance to the owner. Search the latest 72 hours '
                'first; widen to seven days only if needed and label older material. Search both public social discussion '
                'with social.search when available and news/primary sources with web tools when the request concerns both. '
                'A year-wide breakthrough search is not evidence of current news. Distinguish an old result recirculating '
                'in a new post from a genuinely new development. Read the primary source before making scientific claims. '
                'Each draft must identify the actual paper, product, organization, event or debate and a concrete supported '
                'detail explaining what is new. Attach source links and dates outside the draft text. Do not fill the requested '
                'count with generic themes or rephrase one story into several indistinguishable posts. Return fewer good drafts '
                'and explain the gap if needed. Describe something as trending only with observed supporting signals; a single '
                'recent post is merely a recent discussion. Apply these evidence standards to briefs and recommendations too. '
                'When drafts require recent discussions, obtain current source evidence before drafting factual claims. '
                'If that evidence cannot be obtained, explain the specific gap instead of substituting generic promotional posts. '
                'Do not substitute an unrelated inbox summary. Discover useful saved documents when relevant. '
                'For a requested reusable document, return it for private storage. Never assume a connection '
                'is unavailable because an earlier bot message said so; the current tool catalog is authoritative. '
                'Use tasks.overview to find loose ends across conversations. Its groups are historical saved statuses; inspect fresh source_check evidence before saying a reply is still owed. Later SENT messages require review, not an offer to draft an already-sent reply. Use mail tools to read more when snippets are insufficient; unsupported or failed checks mean unverified. Update existing tasks when the owner requests a current reconciliation and fresh evidence clearly establishes completion. Keep one task identity through follow-ups, save next actions and review dates, and close tasks only with evidence. For daily or weekly planning, combine active tasks, deadlines, dependencies, waiting items, calendar availability and relevant email evidence. '
                'Highlight conflicts, preparation and work windows; label assumptions about work hours and task duration. '
                'Suggestions are not completed actions. Only change a task or schedule when the owner requested it; read its latest revision first. '
                'For explicitly requested work that needs later follow-through, save its task and use tasks.delegate. '
                'A request merely to track something or remind the owner is not permission to execute that underlying activity; do not delegate it.',max_calls=10, recovery=self.config.get('recovery'),limits=self.config.get('execution',{}),
                owner_update=lambda:latest(self.home,self.config,context))
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
