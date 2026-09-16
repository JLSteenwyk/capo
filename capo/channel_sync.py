"""Durable conversation labels and transport outbox shared by messaging adapters."""
import hashlib
import json
import sqlite3
import time
from pathlib import Path
from contextlib import contextmanager


class Journal:
    def __init__(self, home, identity):
        root = Path(home) / 'channel-sync'
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = root / (hashlib.sha256(identity.encode()).hexdigest()+'.sqlite3')
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS conversations(code TEXT PRIMARY KEY, thread TEXT UNIQUE NOT NULL);
                CREATE TABLE IF NOT EXISTS incoming(id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS outgoing(id TEXT PRIMARY KEY, thread TEXT NOT NULL,
                    text TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'queued', started REAL, remote_id TEXT);
            ''')
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:yield db
        finally:db.close()

    def get(self, key, default=None):
        with self.connect() as db:
            row = db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def put(self, key, value):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (key,json.dumps(value)))

    def bind(self, fingerprint):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT value FROM meta WHERE key='binding'").fetchone()
            if row and json.loads(row[0]) != fingerprint:
                raise ValueError('Messaging account binding changed; use a new private sync ledger')
            db.execute("INSERT OR IGNORE INTO meta VALUES ('binding',?)", (json.dumps(fingerprint),))

    def code(self, thread):
        code = 'C'+hashlib.sha256(thread.encode()).hexdigest()[:10].upper()
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO conversations VALUES (?,?)', (code,thread))
            row = db.execute('SELECT thread FROM conversations WHERE code=?',(code,)).fetchone()
            if row[0] != thread:raise ValueError('Conversation label collision')
        return code

    def thread(self, code):
        with self.connect() as db:
            row = db.execute('SELECT thread FROM conversations WHERE code=?',(code.upper(),)).fetchone()
        return row[0] if row else None

    def enqueue(self, id, thread, text):
        if not text:return
        code = self.code(thread) if thread != 'help' else None
        # Bound each transport message without truncating the complete answer.
        with self.connect() as db:
            for i, offset in enumerate(range(0,len(text),1800)):
                chunk = (f'[{code}] ' if code else '')+text[offset:offset+1800]
                db.execute('INSERT OR IGNORE INTO outgoing(id,thread,text) VALUES (?,?,?)',
                           (id+':'+str(i),thread,chunk))

    def pending(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM outgoing WHERE state!='sent' ORDER BY rowid LIMIT 20")]

    def begin(self, id):
        with self.connect() as db:
            db.execute("UPDATE outgoing SET state='sending',started=? WHERE id=? AND state='queued'",(time.time(),id))

    def sent(self, id, remote):
        with self.connect() as db:
            db.execute("UPDATE outgoing SET state='sent',remote_id=? WHERE id=?",(remote,id))


class MirroredSlackClient:
    """Capture successful bot posts from all existing Slack workflows."""
    def __init__(self, client, journal, channel):
        self.client, self.journal, self.channel = client, journal, channel

    def __getattr__(self, name):return getattr(self.client,name)

    def chat_postMessage(self, **kwargs):
        result = self.client.chat_postMessage(**kwargs)
        if kwargs.get('channel') == self.channel and result.get('ts'):
            thread = kwargs.get('thread_ts') or result['ts']
            import html
            try:
                self.journal.enqueue('slack-bot:'+result['ts'], thread,
                                     'Capo: '+html.unescape(kwargs.get('text','')))
            except Exception:
                # Slack already accepted the post. Do not make its caller send
                # it again because secondary delivery storage failed.
                import logging
                logging.getLogger(__name__).error('Could not persist the iMessage mirror of a delivered Slack message')
        return result
