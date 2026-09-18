"""Bounded structured monitoring reports and durable change-only delivery."""
import hashlib
import json
import re
from urllib.parse import urlsplit
from .contracts import TEXT, TEXTS, object_schema, validate
from .conversation import _write
from .research_tools import ReadTool

FINDING = object_schema({'key': TEXT, 'version': TEXT, 'summary': TEXT, 'source': TEXT})
REPORT = object_schema({'findings': {'type': 'array', 'items': FINDING},
                        'blockers': TEXTS, 'coverage': TEXT})


class AssignmentReport:
    def __init__(self, directory, interests=None):
        self.interests = {v['key'] for v in (interests or [])}
        self.schema = REPORT
        if self.interests:
            finding = object_schema({**FINDING['properties'], 'interest_key': TEXT,
                'relationship': {'type':'string','enum':['none','direct','related']}, 'why': TEXT})
            self.schema = object_schema({**REPORT['properties'], 'findings':{'type':'array','items':finding}})
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


def fingerprint(item):
    return hashlib.sha256(json.dumps([item['key'], item['version']], ensure_ascii=False).encode()).hexdigest()


def finish(run, report, previous):
    seen = {fingerprint(item) for item in previous.get('findings', [])}
    new = [item for item in report['findings'] if fingerprint(item) not in seen]
    blockers = [item for item in report['blockers'] if item not in previous.get('blockers', [])]
    run['report'] = report
    if not new and not blockers:
        run['status'] = 'quiet'
        return
    # Deliver the bounded actual findings, never an empty model preamble.
    lines = [run['title']]
    for item in new[:3]:
        # Keep internal mail/document IDs in the saved report, not the Slack update.
        try:
            source=urlsplit(item['source'])
            link=item['source'] if source.scheme in ('https', 'http') and source.netloc and not source.username else ''
        except ValueError:
            link=''
        from .personalization import finding_text
        lines.append('- ' + finding_text(item) + (' (' + link + ')' if link else ''))
    if len(new) > 3:
        lines.append(f'{len(new)-3} more findings saved. Ask Capo for the full check.')
    lines.extend('- Needs attention: ' + item for item in blockers[:2])
    run.update(status='ready', payload={'text': '\n'.join(lines), 'news': []})
