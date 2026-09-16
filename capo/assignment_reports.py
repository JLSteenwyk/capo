"""Bounded structured monitoring reports and durable change-only delivery."""
import hashlib
import json
from .contracts import TEXT, TEXTS, object_schema, validate
from .conversation import _write
from .research_tools import ReadTool

FINDING = object_schema({'key': TEXT, 'version': TEXT, 'summary': TEXT, 'source': TEXT})
REPORT = object_schema({'findings': {'type': 'array', 'items': FINDING},
                        'blockers': TEXTS, 'coverage': TEXT})


class AssignmentReport:
    def __init__(self, directory):
        self.path = directory / 'assignment-report.json'

    def record(self, **report):
        validate(report, REPORT)
        if (len(report['findings']) > 10 or len(report['blockers']) > 5
                or not report['coverage'].strip() or len(json.dumps(report)) > 12000):
            raise ValueError('Use a bounded report with coverage, up to ten findings and five blockers')
        keys = set()
        for item in report['findings']:
            if any(not value.strip() or len(value) > 1200 for value in item.values()) or item['key'] in keys:
                raise ValueError('Findings need unique stable keys, versions, summaries and evidence sources')
            keys.add(item['key'])
        if any(not item.strip() or len(item) > 1000 for item in report['blockers']):
            raise ValueError('Use short specific blockers')
        _write(self.path, report)
        return {'recorded': True, 'report': report}

    def tool(self):
        return ReadTool('monitor.report',
            'Record this check’s findings, blockers and coverage before finishing. Local run bookkeeping only. '
            'Every actionable finding needs a stable key (source/entity), a version describing meaningful facts '
            '(not today’s date or wording), a short summary and an inspected evidence source. '
            'Reuse prior keys/versions if facts did not change. Empty findings means nothing actionable in checked sources. '
            'Missing access or incomplete essential checks belong in blockers; never treat failures as a clean check.',
            REPORT, self.record)

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
    lines.extend('- ' + item['summary'] + ' (' + item['source'] + ')' for item in new[:3])
    if len(new) > 3:
        lines.append(f'{len(new)-3} more findings saved. Ask Capo for the full check.')
    lines.extend('- Needs attention: ' + item for item in blockers[:2])
    run.update(status='ready', payload={'text': '\n'.join(lines), 'news': []})
