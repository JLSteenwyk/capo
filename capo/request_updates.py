"""Read accepted owner follow-ups directly from the durable Slack inbox."""
import json
import re
import sqlite3
from pathlib import Path
from urllib.parse import quote


def latest(home,config,context):
    event_id=context.get('request_event');thread=context.get('request_thread')
    path=Path(home)/'capo.sqlite3'
    if not event_id or not thread or not path.exists():return None
    db=sqlite3.connect('file:'+quote(str(path.resolve()),safe='/')+'?mode=ro',uri=True,timeout=10)
    db.row_factory=sqlite3.Row
    try:
        anchor=db.execute('SELECT rowid FROM slack_inbox WHERE id=?',(event_id,)).fetchone()
        if not anchor:return None
        from .slack import owner_message
        for row in db.execute('SELECT id,data FROM slack_inbox WHERE rowid>? ORDER BY rowid DESC',(anchor[0],)):
            body=json.loads(row['data']);event=body.get('event',{})
            if not owner_message(config,body) or event.get('type') not in ('message','app_mention'):continue
            if event.get('thread_ts',event.get('ts'))!=thread:continue
            # Only accepted owner events are stored by ingest. Recheck identity
            # here; tools and model-generated notes cannot insert authority.
            text=re.sub(r'^\s*<@[A-Z0-9]+>[\s,:]*','',event.get('text','')).strip()
            if re.fullmatch(r'(?:help|status|details|sync)(?:\s+[a-f0-9]{16})?',text,re.I):continue
            return row['id']
        return None
    finally:db.close()
