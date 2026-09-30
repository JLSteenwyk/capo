"""Bounded structured monitoring reports and durable change-only delivery."""
import hashlib
import json
import re
from datetime import date, datetime
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo
from .contracts import TEXT, TEXTS, object_schema, validate
from .conversation import _write
from .research_tools import ReadTool

FINDING = object_schema({'key': TEXT, 'version': TEXT, 'summary': TEXT, 'source': TEXT, 'due': TEXT})
# `due` is optional so saved reports and older callers stay valid.
FINDING['required'] = ['key', 'version', 'summary', 'source']
DUE_SOON_DAYS = 3
REPORT = object_schema({'findings': {'type': 'array', 'items': FINDING},
                        'blockers': TEXTS, 'coverage': TEXT, 'checked': TEXTS})
# `checked` (short names of entities actually inspected) is optional; it lets
# recurring checks rotate coverage and tell the owner what was covered.
REPORT['required'] = ['findings', 'blockers', 'coverage']


class AssignmentReport:
    def __init__(self, directory, interests=None):
        self.interests = {v['key'] for v in (interests or [])}
        self.schema = REPORT
        if self.interests:
            finding = object_schema({**FINDING['properties'], 'interest_key': TEXT,
                'relationship': {'type':'string','enum':['none','direct','related']}, 'why': TEXT})
            finding['required'] = [k for k in finding['required'] if k != 'due']
            self.schema = object_schema({**REPORT['properties'], 'findings':{'type':'array','items':finding}})
            self.schema['required'] = REPORT['required']
        self.path = directory / 'assignment-report.json'

    def record(self, operation_id=None, **report):
        validate(report, self.schema)
        if (len(report['findings']) > 10 or len(report['blockers']) > 5
                or not report['coverage'].strip() or len(json.dumps(report)) > 12000):
            raise ValueError('Use a bounded report with coverage, up to ten findings and five blockers')
        keys = set()
        for item in report['findings']:
            if any(not item[k].strip() or len(item[k]) > 1200 for k in ('key','version','summary','source')) or item['key'] in keys:
                raise ValueError('Findings need unique stable keys, versions, summaries and evidence sources')
            keys.add(item['key'])
            if item.get('due') and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', item['due']):
                raise ValueError('Use due as YYYY-MM-DD, or leave it empty')
            if self.interests:
                if item['relationship']=='none':
                    if item['interest_key'] or item['why']:raise ValueError('Unpersonalized findings must leave interest_key and why empty')
                elif item['interest_key'] not in self.interests or not item['why'].strip() or len(item['why'])>160:
                    raise ValueError('Personalized findings need a supplied interest_key and a short grounded explanation')
            if re.fullmatch(r'receipt(?:_index| indexes?|s)?[\s:#\d,]+', item['source'], re.I):
                raise ValueError('Finding sources must be reviewable: copy the relevant html_url from the inspected result, '
                                 'not receipt numbers. For private sources without a browser link, use the actual source identifier.')
        if any(not item.strip() or len(item) > 1000 for item in report['blockers']):
            raise ValueError('Use short specific blockers')
        if len(report.get('checked', [])) > 30 or any(not n.strip() or len(n) > 80 for n in report.get('checked', [])):
            raise ValueError('List at most 30 checked names, each under 80 characters')
        _write(self.path, report)
        return {'recorded': True, 'saved': True, 'report': report}

    def tool(self):
        return ReadTool('monitor.report',
            'Record this check’s findings, blockers and coverage before finishing. Local run bookkeeping only. '
            'Every actionable finding needs a stable key (source/entity), a version describing meaningful facts '
            '(not today’s date or wording), a short summary and an inspected evidence source. '
            'Use the relevant browser URL (html_url) as source whenever available, especially for PRs, issues, '
            'CI runs and security alerts. Never use receipt indexes as sources. Include the repository in GitHub summaries. '
            'Reuse prior keys/versions if facts did not change. Empty findings means nothing actionable in checked sources. '
            'Set due (YYYY-MM-DD) only for a verified deadline, renewal, expiry or event date the owner may need to act on; '
            'the host uses it to remind the owner shortly before that date. '
            'List in checked the short names of the entities you actually inspected (for example artists or repositories), '
            'not ones you only planned to check. '
            'Missing access or incomplete essential checks belong in blockers; never treat failures as a clean check. '
            'Ordinary page/window limits belong in coverage, not blockers, unless they prevent the requested check. '
            'Do not request bank access or other integrations that the assignment does not require. '
            'When relationship fields are present, use direct for a supported match, related for a suggested connection, '
            'and none with empty interest_key/why when preferences are irrelevant. Cite a supplied interest key and explain relevance in at most 160 characters.',
            self.schema, self.record, mutates=True, finalizes=True)

    def read(self):
        if not self.path.exists():
            raise ValueError('Monitoring finished without a structured coverage report')
        return json.loads(self.path.read_text())


def previous_report(db, owner, schedule_id, revision, before):
    # Only delivered/quiet results are a baseline. Failed or unsent results must not suppress alerts.
    rows = db.db.execute("SELECT data FROM runs WHERE scope=? AND status IN ('sent','quiet') "
        "AND json_extract(data, '$.schedule_id')=? AND json_extract(data, '$.revision')=? "
        "AND json_extract(data, '$.created')<? ORDER BY json_extract(data, '$.created') DESC LIMIT 1",
        (owner, schedule_id, revision, before)).fetchone()
    return json.loads(rows[0]).get('report', {}) if rows else {}


def research_context(previous):
    """Keep deduplication history without recycling historical failure prose.

    The full report stays in host storage for delivery comparison. Pending IDs
    and cursors remain available so actual unfinished inspections can continue.
    """
    previous={k:v for k,v in previous.items() if k!='reminded'}  # host delivery bookkeeping
    if not previous.get('blockers'):return previous
    return {**previous,'blockers':[], 'historical_blocker_count':len(previous['blockers']),
            'coverage':'Historical findings only. Recheck relevant sources; prior blockers are not evidence of a current failure.'}


def fingerprint(item):
    return hashlib.sha256(json.dumps([item['key'], item['version']], ensure_ascii=False).encode()).hexdigest()


def due_soon(item, today):
    """Whether a finding's verified action date falls within the reminder window."""
    try:
        days = (date.fromisoformat(item.get('due') or '') - today).days
    except ValueError:
        return False
    return 0 <= days <= DUE_SOON_DAYS


def finish(run, report, previous, timezone='UTC'):
    """Deliver new findings and due-date reminders only.

    A previously reported finding with an action date is shown once more shortly
    before that date, so an unchanged renewal is not silently dropped. Blockers
    stay in the saved report: health checks surface incomplete or failed runs in
    plain language, so model-written coverage notes are never posted as alerts.
    A follow-up in the original's thread adds new findings only.
    """
    seen = {fingerprint(item) for item in previous.get('findings', [])}
    new = [item for item in report['findings'] if fingerprint(item) not in seen]
    followup = bool(run.get('recovery_of') and run.get('thread_ts'))
    today = datetime.fromtimestamp(run.get('created', 0), ZoneInfo(timezone)).date()
    reminded = set(previous.get('reminded', []))
    upcoming = [] if followup else [item for item in report['findings'] if fingerprint(item) in seen
                                    and fingerprint(item) not in reminded and due_soon(item, today)]
    report['reminded'] = sorted(reminded | {fingerprint(item) for item in upcoming})
    run['report'] = report
    if not new and not upcoming:
        if run.get('verification_incomplete') and not followup:
            # A host-detected failure is still announced, in plain words.
            run.update(status='ready', payload={'text': run['title'] + ': this check could not finish, so nothing new '
                       'was confirmed. It will run again at its next scheduled time.', 'news': []})
        elif run.get('status') != 'failed':  # Never mask a failed run as a clean check.
            run['status'] = 'quiet'
        return
    # Deliver the bounded actual findings, never an empty model preamble.
    lines = ['Follow-up: new since the earlier check' if followup else run['title']]
    from .personalization import finding_text
    for item in new[:3]:
        lines.append('- ' + finding_text(item) + _link(item))
    if len(new) > 3:
        lines.append(f'{len(new)-3} more findings saved. Ask Capo for the full check.')
    for item in upcoming[:3]:
        when = date.fromisoformat(item['due'])
        label = 'today' if when == today else when.strftime('%a %b %-d')
        lines.append(f'- Reminder, {label}: ' + finding_text(item) + _link(item))
    run.update(status='ready', payload={'text': '\n'.join(lines), 'news': []})


def _link(item):
    # Keep internal mail/document IDs in the saved report, not the Slack update.
    try:
        source = urlsplit(item['source'])
        return ' (' + item['source'] + ')' if source.scheme in ('https', 'http') and source.netloc and not source.username else ''
    except ValueError:
        return ''
