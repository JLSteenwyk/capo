"""Run actual shared calendar adapters against an isolated synthetic world."""
import hashlib
import importlib.util
import inspect
import json
import time
import subprocess
from pathlib import Path
from unittest.mock import patch
from contextlib import ExitStack

from .calendar_cases import CASES,CalendarWorld
from .mail_cases import CASES as MAIL_CASES,MailWorld
from .github_cases import CASES as GITHUB_CASES,GitHubWorld
from .task_cases import CASES as TASK_CASES,TaskWorld
from .research_cases import CASES as RESEARCH_CASES,ResearchWorld


def run_case(provider,name,output,mode='scripted',recovery_wait_seconds=0):
    if mode not in ('scripted','model'):raise ValueError('Label the evaluation provider mode')
    if type(recovery_wait_seconds) is not int or not 0<=recovery_wait_seconds<=600:
        raise ValueError('Recovery wait must be between zero and 600 seconds')
    output=Path(output).resolve()
    repository=Path(__file__).resolve().parents[2]
    if output==repository or repository in output.parents:
        raise ValueError('Evaluation artifacts must be outside the public repository')
    if output.exists() and any(output.iterdir()):raise ValueError('Use a fresh evaluation directory')
    output.mkdir(parents=True,mode=0o700,exist_ok=True)
    case={**CASES,**MAIL_CASES,**GITHUB_CASES,**TASK_CASES,**RESEARCH_CASES}[name]
    world=ResearchWorld(case) if case.get('web') else TaskWorld(case) if case.get('tasks') else GitHubWorld(case) if case.get('github') else (MailWorld(case) if case.get('mail') else CalendarWorld(case))
    from capo.capabilities import shared_tools,Documents
    from capo.research_tools import ReadTools,research
    from capo.conversation import _write
    config={'repositories':{},'calendar':{'enabled':True,'timezone':'America/Los_Angeles'}}
    if case.get('mail'):config['gmail']={'enabled':True,'drafts':bool(case.get('draft'))}
    if case.get('github'):
        from capo.repository import git
        repo=output/'repository';repo.mkdir()
        git(repo,'init','-b','main');git(repo,'remote','add','origin','https://github.com/example/project.git')
        config['repositories']={'project':{'path':str(repo)}}
    request={'message':case['request'],'timezone':'America/Los_Angeles'}
    correction={}
    correction_args={}
    if case.get('correction'):
        from .corrections import CorrectionProvider
        provider=CorrectionProvider(provider)
        if 'owner_update' in inspect.signature(research).parameters:
            correction_args={'owner_update':provider.update}
    started=time.monotonic()
    # Patch both the lazy lookup and the action module's imported class; neither
    # may retain a real client or a different scenario's mock between cases.
    import capo.calendar_actions
    with ExitStack() as stack:
        stack.enter_context(patch('capo.calendar.GoogleCalendar',side_effect=world.client))
        stack.enter_context(patch('capo.calendar_actions.GoogleCalendar',side_effect=world.client))
        if case.get('web'):
            stack.enter_context(patch('capo.web_tools.WebTools.search',side_effect=world.search))
            stack.enter_context(patch('capo.web_tools.read_page',side_effect=world.read))
        if case.get('mail'):stack.enter_context(patch('capo.gmail.Gmail',side_effect=world.mail_client))
        if case.get('github') and importlib.util.find_spec('capo.github_tools') is not None:
            stack.enter_context(patch('capo.github_tools.ReadClient.get',side_effect=world.github_get))
        registry=shared_tools(output/'state',config,Documents(output/'state','synthetic'),request)
        # Only these adapters are backed by this fixture. No web, GitHub, or
        # personal-service fallback is allowed during a calendar-only evaluation.
        allowed=('calendar.','dates.','clock.','mail.') if case.get('mail') else ('calendar.','dates.','clock.')
        if case.get('github'):allowed+=('github.',)
        if case.get('tasks'):allowed+=('tasks.',)
        if case.get('web'):allowed+=('web.',)
        tools=ReadTools([tool for name,tool in registry.tools.items() if name.startswith(allowed) and name!='github.issues'])
        from capo.recovery import RetryLater
        waited=0
        while True:
            try:
                result=research(provider,tools,request,output/'request',max_calls=10,**correction_args)
                break
            except RetryLater as exc:
                delay=max(1,exc.retry_at-time.time())
                if waited+delay>recovery_wait_seconds:raise
                # Keep the same simulated world and adapter discovery caches.
                # Never recreate a world after a tool might have changed it.
                time.sleep(delay);waited+=delay
        if case.get('correction'):
            _write(output/'first-stage.json',result)
            correction={'triggered':provider.triggered,'monitor_supported':bool(correction_args),
                        'first_status':result.get('status','unassessed'),'first_tool_attempts':len(result.get('receipts',[])),
                        'writes_before_followup':len(world.writes)}
            provider.armed=False
            request={**request,'message':case['request']+'\nOwner correction: '+case['correction'],
                     'original_objective':case['request'],'owner_correction':case['correction'],
                     'previous_receipts':result.get('receipts',[])}
            while True:
                try:
                    result=research(provider,tools,request,output/'followup',max_calls=10)
                    break
                except RetryLater as exc:
                    delay=max(1,exc.retry_at-time.time())
                    if waited+delay>recovery_wait_seconds:raise
                    time.sleep(delay);waited+=delay
    if case.get('tasks'):
        world.task_rows=registry.call('tasks.search',{'query':'','status':'all','cursor':''})['tasks']
    checks=world.grade(result)
    if correction:
        checks['correction_delivered']=correction['triggered']
        checks['superseded_write_prevented']=correction['writes_before_followup']==0
    revision=subprocess.run(['git','rev-parse','HEAD'],cwd=repository,capture_output=True,text=True,check=True).stdout.strip()
    source_hash=hashlib.sha256()
    for path in sorted((repository/'capo').rglob('*.py')):
        if 'evaluations' in path.relative_to(repository/'capo').parts:continue
        source_hash.update(str(path.relative_to(repository)).encode()+b'\0'+path.read_bytes())
    success=False if any(v is False for v in checks.values()) else (None if any(v is None for v in checks.values()) else True)
    summary={'case':name,'split':case['split'],'mode':mode,'checks':checks,
        'automated_checks_passed':success,'task_success':None if success and (case.get('mail') or case.get('web')) else success,
        'semantic_review_required':bool(case.get('mail') or case.get('web')),
        'available_tools':sorted(tools.tools),'correction':correction,
        'recovery_wait_seconds':waited,
        'elapsed_seconds':round(time.monotonic()-started,3),'tool_attempts':len(result.get('receipts',[]))+correction.get('first_tool_attempts',0),
        'remote_writes':len(world.writes),'reported_status':result.get('status','unassessed'),
        'unnecessary_clarification':None,'unsupported_claims':None,
        'fixture_sha256':hashlib.sha256(json.dumps(case,sort_keys=True).encode()).hexdigest(),
        'revision':revision,'implementation_sha256':source_hash.hexdigest(),
        'harness_sha256':hashlib.sha256(b''.join(path.read_bytes() for path in sorted(Path(__file__).parent.glob('*.py')))).hexdigest(),
        'coverage':'Actual shared reasoning loop and calendar/Gmail/GitHub inspection adapters with synthetic services. Excludes Slack routing/delivery and other integrations. Fact-token checks do not prove full semantic quality; answer claims and clarification require separate review.'}
    _write(output/'summary.json',summary)
    _write(output/'world.json',{'before':world.original,'after':world.calendars,'writes':world.writes,
                              'drafts':getattr(world,'drafts',{}),'mail_reads':getattr(world,'mail_reads',[]),
                              'github_reads':getattr(world,'github_reads',[]),'tasks':getattr(world,'task_rows',[]),'web_reads':getattr(world,'web_reads',[])})
    return summary
