"""Cross-automation deduplication from structured sources and meaningful versions."""
import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path
from .digest import scope

WINDOW_SECONDS=7*24*3600


def notice(item,kind):
    # Roles may use different local keys for the same inspected source. Versions
    # describe meaningful facts, not wording or today's date (monitor contract).
    token=[item['source'].strip(),item['version'],kind]
    if kind=='reminder':token.append(item['due'])
    return {'id':hashlib.sha256(json.dumps(token,ensure_ascii=False).encode()).hexdigest(),
            'kind':kind,'item':item}


def render(heading,notices,today):
    from .assignment_reports import _link
    from .personalization import finding_text
    changes=[n['item'] for n in notices if n['kind']=='change']
    reminders=[n['item'] for n in notices if n['kind']=='reminder']
    if not changes and not reminders:return ''
    lines=[heading]
    for item in changes[:3]:lines.append('- '+finding_text(item)+_link(item))
    if len(changes)>3:lines.append(f'{len(changes)-3} more findings saved. Ask Capo for the full check.')
    for item in reminders[:3]:
        when=date.fromisoformat(item['due'])
        label='today' if when==today else when.strftime('%a %b %-d')
        lines.append(f'- Reminder, {label}: '+finding_text(item)+_link(item))
    if len(reminders)>3:lines.append(f'{len(reminders)-3} more upcoming findings saved. Ask Capo for the full check.')
    return '\n'.join(lines)


def prior(home,config,key,now):
    """Unconfirmed sends reserve notices; failed and quiet reports do not."""
    seen=set()
    for rel in ('digest/digest.sqlite3','heartbeat/digest/digest.sqlite3','scheduled/digest/digest.sqlite3'):
        path=Path(home)/rel
        if not path.exists():continue
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=10)) as db:
            rows=db.execute("SELECT key,data FROM runs WHERE scope=? AND (updated>=? OR status IN ('sending','delivery_unknown')) AND status IN ('sent','sending','delivery_unknown') LIMIT 2001",
                            (scope(config),now.timestamp()-WINDOW_SECONDS)).fetchall()
            if len(rows)>2000:raise ValueError('Notification history window exceeded its scan limit')
            for saved_key,data in rows:
                if saved_key==key:continue
                seen.update(n['id'] for n in json.loads(data).get('payload',{}).get('finding_notices',[]))
    return seen


def filter_findings(manager,run,now):
    """Under the common delivery lock; never rewrite uncertain wire payloads."""
    if run['status']!='ready' or not run.get('payload',{}).get('finding_notices'):return True
    payload=run['payload'];seen=prior(manager.service.store.home,manager.service.config,run['key'],now)
    kept=[n for n in payload['finding_notices'] if n['id'] not in seen]
    if len(kept)==len(payload['finding_notices']):return True
    run['deduplicated_findings']=sorted(set(run.get('deduplicated_findings',[])) | (seen & {n['id'] for n in payload['finding_notices']}))
    payload['finding_notices']=kept
    payload['text']=render(payload['finding_heading'],kept,date.fromisoformat(payload['finding_day']))
    run.pop('wire_text',None);run.pop('wire_mrkdwn',None)
    if not kept:run['status']='quiet'
    manager.db.save(run,now)
    return run['status']=='ready'
