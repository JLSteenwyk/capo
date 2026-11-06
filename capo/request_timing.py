"""Private request lifecycle telemetry, independent of transcript artifacts."""
import hashlib
import sqlite3
import time
from contextlib import contextmanager,closing
from pathlib import Path

PHASES={'message','received','acknowledged','execution_started','provider_started','provider_finished',
        'tool_started','tool_finished','completed','first_reply','delivered'}


class RequestTiming:
    def __init__(self,home,event_id):
        self.path=Path(home)/'request-timing.sqlite3'
        self.key=hashlib.sha256(str(event_id).encode()).hexdigest()

    def mark(self,phase,*,at=None,label=''):
        if phase not in PHASES:raise ValueError('Unknown lifecycle phase')
        # Catalog names and fixed statuses only; never arbitrary provider payloads.
        if not isinstance(label,str) or len(label)>80 or any(not (c.isalnum() or c in '._-') for c in label):raise ValueError('Invalid timing label')
        at=time.time() if at is None else at
        with closing(sqlite3.connect(self.path,timeout=10)) as db,db:
            db.execute('CREATE TABLE IF NOT EXISTS lifecycle(seq INTEGER PRIMARY KEY,request TEXT,phase TEXT,at REAL,label TEXT)')
            if phase not in ('provider_started','provider_finished','tool_started','tool_finished'):
                if db.execute('SELECT 1 FROM lifecycle WHERE request=? AND phase=?',(self.key,phase)).fetchone():return
            db.execute('INSERT INTO lifecycle(request,phase,at,label) VALUES (?,?,?,?)',(self.key,phase,at,label))
        self.path.chmod(0o600)

    @contextmanager
    def activity(self,kind,label):
        self.mark(kind+'_started',label=label)
        try:yield
        finally:self.mark(kind+'_finished',label=label)

    def read(self):
        if not self.path.exists():return []
        with closing(sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True)) as db:
            return [{'phase':p,'at':a,'label':l} for p,a,l in db.execute('SELECT phase,at,label FROM lifecycle WHERE request=? ORDER BY seq',(self.key,))]
