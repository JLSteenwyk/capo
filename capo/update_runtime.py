"""Service-side drain handshake and private health receipts for the external updater."""
import fcntl
import json
import os
import sqlite3
import time
from pathlib import Path
from .conversation import _write

EXCLUDED={'development','workspaces','baselines','updates'}


def idle_reason(home):
    home=Path(home)
    dbpath=home/'capo.sqlite3'
    if dbpath.exists():
        db=sqlite3.connect('file:'+str(dbpath)+'?mode=ro',uri=True)
        try:
            if db.execute("SELECT COUNT(*) FROM objectives WHERE json_extract(data,'$.status') IN ('queued','running')").fetchone()[0]:return 'development work'
            if db.execute('SELECT COUNT(*) FROM slack_inbox WHERE handled=0').fetchone()[0]:return 'pending messages'
        finally:db.close()
    for path in home.rglob('digest.sqlite3'):
        if any(p in EXCLUDED for p in path.relative_to(home).parts):continue
        db=sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True)
        try:
            if db.execute("SELECT COUNT(*) FROM runs WHERE status IN ('queued','building','ready','sending')").fetchone()[0]:return 'scheduled work'
        finally:db.close()
    for path in (home/'browser/sessions').glob('*/state.json'):
        if json.loads(path.read_text()).get('status') not in ('cancelled','completed','blocked'):return 'browser work'
    for path in home.rglob('*.lock'):
        if any(p in EXCLUDED for p in path.relative_to(home).parts) or path.name=='slack-service.lock':continue
        try:
            with path.open('r') as handle:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return 'active worker'
    return ''


def health(home, connected, release, cycle_completed=False):
    """Called on the service loop. New arrivals are durably queued while drained."""
    root=Path(home)/'updates';root.mkdir(parents=True,exist_ok=True,mode=0o700)
    path=root/'drain.json';drain={}
    if path.exists():
        try:drain=json.loads(path.read_text())
        except (ValueError,OSError):pass
    probation={}
    if (root/'probation.json').exists():probation=json.loads((root/'probation.json').read_text())
    paused=probation.get('release')==release
    valid=drain.get('expires',0)>time.time() and isinstance(drain.get('nonce'),str)
    reason=idle_reason(home) if valid and not paused else ''
    drained=bool(valid and connected and not reason)
    _write(root/'health.json',dict(pid=os.getpid(),release=release,connected=bool(connected),
        at=time.time(),drained=drained,nonce=drain.get('nonce') if drained else '',
        cycle_completed=cycle_completed,waiting_for=reason,probation=paused))
    return drained or paused
