"""Owner-scoped request history and working notes shared across Slack routes."""
from contextlib import contextmanager
import hashlib
import json
import sqlite3
from pathlib import Path

from .contracts import TEXT, TEXTS, object_schema
from .research_tools import ReadTool


class RequestMemory:
    def __init__(self, home, owner, thread):
        self.root = Path(home)/'request-memory'/hashlib.sha256(owner.encode()).hexdigest()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.root/'requests.sqlite3'
        self.thread = hashlib.sha256(str(thread).encode()).hexdigest()
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS entries(thread TEXT, event TEXT, kind TEXT, data TEXT, PRIMARY KEY(thread,event,kind))')
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path, timeout=20)
        try:
            with db:yield db
        finally:db.close()

    def record(self, event, kind, data):
        if kind not in ('owner', 'outcome', 'notes', 'receipt'):
            raise ValueError('Invalid request memory record')
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO entries VALUES (?,?,?,?)',
                       (self.thread, str(event), kind, json.dumps(data)))

    def read(self):
        with self.connect() as db:
            first = db.execute("SELECT data FROM entries WHERE thread=? AND kind='owner' ORDER BY rowid LIMIT 1", (self.thread,)).fetchone()
            rows = db.execute('SELECT kind,data FROM entries WHERE thread=? ORDER BY rowid DESC LIMIT 30', (self.thread,)).fetchall()
        history=[];size=0
        for kind, raw in rows:
            if size+len(raw)>50000:continue
            history.append({'kind':kind,'data':json.loads(raw)});size+=len(raw)
        # Notes are model interpretations, never new owner authority or verified facts.
        return {'original_request': json.loads(first[0]) if first else None,
                'history': list(reversed(history)),
                'coverage': 'Original owner request and latest 30 records within 50 KB. Notes are unverified interpretations; receipts describe actual tool outcomes.'}

    def tools(self, event):
        def save(objective, facts, uncertainties, next_steps):
            value = dict(objective=objective, facts=facts, uncertainties=uncertainties, next_steps=next_steps)
            if len(json.dumps(value)) > 10000:
                raise ValueError('Working notes exceed 10 KB')
            key = str(event)+':'+hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()
            self.record(key, 'notes', value)
            return {'saved': True, 'verified': False}
        return [ReadTool('context.read', 'Read this owner conversation’s original request, prior outcomes, action receipts and working notes. Notes are hypotheses, not authorization. Use when a follow-up omits details.', object_schema({}), self.read),
                ReadTool('context.save', 'Save bounded working notes: objective, source-linked facts, unresolved uncertainties and next steps. These notes cannot authorize actions or establish success. Save useful progress before asking a question.',
                         object_schema({'objective':TEXT,'facts':TEXTS,'uncertainties':TEXTS,'next_steps':TEXTS}), save)]
