"""Run actual shared calendar adapters against an isolated synthetic world."""
import hashlib
import json
import time
import subprocess
from pathlib import Path
from unittest.mock import patch

from .calendar_cases import CASES,CalendarWorld


def run_case(provider,name,output,mode='scripted'):
    if mode not in ('scripted','model'):raise ValueError('Label the evaluation provider mode')
    output=Path(output).resolve()
    repository=Path(__file__).resolve().parents[2]
    if output==repository or repository in output.parents:
        raise ValueError('Evaluation artifacts must be outside the public repository')
    if output.exists() and any(output.iterdir()):raise ValueError('Use a fresh evaluation directory')
    output.mkdir(parents=True,mode=0o700,exist_ok=True)
    case=CASES[name];world=CalendarWorld(case)
    from capo.capabilities import shared_tools,Documents
    from capo.research_tools import ReadTools,research
    from capo.conversation import _write
    config={'repositories':{},'calendar':{'enabled':True,'timezone':'America/Los_Angeles'}}
    request={'message':case['request'],'timezone':'America/Los_Angeles'}
    started=time.monotonic()
    # Patch both the lazy lookup and the action module's imported class; neither
    # may retain a real client or a different scenario's mock between cases.
    import capo.calendar_actions
    with patch('capo.calendar.GoogleCalendar',side_effect=world.client),patch('capo.calendar_actions.GoogleCalendar',side_effect=world.client):
        registry=shared_tools(output/'state',config,Documents(output/'state','synthetic'),request)
        # Only these adapters are backed by this fixture. No web, GitHub, or
        # personal-service fallback is allowed during a calendar-only evaluation.
        tools=ReadTools([tool for name,tool in registry.tools.items() if name.startswith(('calendar.','dates.','clock.'))])
        result=research(provider,tools,request,output/'request',max_calls=10)
    checks=world.grade(result)
    revision=subprocess.run(['git','rev-parse','HEAD'],cwd=repository,capture_output=True,text=True,check=True).stdout.strip()
    source_hash=hashlib.sha256()
    for path in sorted((repository/'capo').rglob('*.py')):
        if 'evaluations' in path.relative_to(repository/'capo').parts:continue
        source_hash.update(str(path.relative_to(repository)).encode()+b'\0'+path.read_bytes())
    success=False if any(v is False for v in checks.values()) else (None if any(v is None for v in checks.values()) else True)
    summary={'case':name,'split':case['split'],'mode':mode,'checks':checks,'task_success':success,
        'elapsed_seconds':round(time.monotonic()-started,3),'tool_attempts':len(result.get('receipts',[])),
        'remote_writes':len(world.writes),'reported_status':result.get('status','unassessed'),
        'unnecessary_clarification':None,'unsupported_claims':None,
        'fixture_sha256':hashlib.sha256(json.dumps(case,sort_keys=True).encode()).hexdigest(),
        'revision':revision,'implementation_sha256':source_hash.hexdigest(),
        'harness_sha256':hashlib.sha256(Path(__file__).read_bytes()+Path(__file__).with_name('calendar_cases.py').read_bytes()).hexdigest(),
        'coverage':'Actual shared reasoning loop and calendar adapters with fake Google state. Excludes Slack routing/delivery and other integrations. Semantic answer claims and clarification require separate review.'}
    _write(output/'summary.json',summary)
    _write(output/'world.json',{'before':world.original,'after':world.calendars,'writes':world.writes})
    return summary
