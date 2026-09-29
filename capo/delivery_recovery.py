"""Bound uncertain delivery reconciliation without authorizing another send."""


NOT_FOUND_CHECKS = 3
CONCLUDE_AFTER = 24 * 3600


def defer(run, now, history_complete=False):
    """Keep an unconfirmed post uncertain; never authorize another send.

    Only repeated complete history checks spanning a day, all without the
    message, establish that it was not posted. It then becomes `undelivered`,
    a final state that is neither resent nor alerted again.
    """
    run['reconcile_attempts']=run.get('reconcile_attempts',0)+1
    if history_complete:run['not_found_checks']=run.get('not_found_checks',0)+1
    age=now-run.get('sending_at',run.get('created',now))
    if history_complete and run.get('not_found_checks',0)>=NOT_FOUND_CHECKS and age>=CONCLUDE_AFTER:
        run.update(status='undelivered',delivery_error='not_found',
                   error_summary='This message was never posted to Slack; it was not resent because it is out of date.')
        return
    parked=run.get('status')=='delivery_unknown' or run['reconcile_attempts']>=5 or age>=900
    run.update(status='delivery_unknown' if parked else 'sending',
               retry_at=now+(3600 if parked else 60),delivery_error='unconfirmed')
    if parked:
        run['error_summary']='Slack delivery is unconfirmed. The saved message will only be checked in history, never automatically resent.'


LABELS={'digest/digest.sqlite3':'The morning digest','heartbeat/digest/digest.sqlite3':'An hourly check',
        'reminders/digest/digest.sqlite3':'A reminder','scheduled/digest/digest.sqlite3':'A scheduled check'}


def pending(home, config):
    import json
    import sqlite3
    import hashlib
    from pathlib import Path
    from .digest import scope
    items=[]
    for rel in ('digest/digest.sqlite3','heartbeat/digest/digest.sqlite3',
                'scheduled/digest/digest.sqlite3','reminders/digest/digest.sqlite3'):
        path=Path(home)/rel
        if not path.exists():continue
        db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)
        try:
            for key,data in db.execute("SELECT key,data FROM runs WHERE scope=? AND status='delivery_unknown' LIMIT 20",(scope(config),)):
                run=json.loads(data)
                items.append({'id':'delivery-unknown:'+hashlib.sha256(key.encode()).hexdigest(),
                    'kind':'connection','title':'Report delivery could not be confirmed',
                    'status':'delivery_unknown','report':run.get('title') or LABELS.get(rel,'A Capo message'),
                    'started_at':run.get('sending_at',run.get('created')),
                    'next_action':run.get('error_summary','Check Slack history; do not resend blindly.')})
        finally:db.close()
    return items[:20]
