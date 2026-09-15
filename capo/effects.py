"""Durable external-action receipts: uncertain outcomes are never replayed blindly."""
import hashlib
import json
import sqlite3
from pathlib import Path


class UncertainEffect(ValueError):
    """A request may have succeeded; reconcile it before another write."""


class Effects:
    def __init__(self, home, owner):
        root=Path(home)/'effects'/hashlib.sha256(owner.encode()).hexdigest()
        root.mkdir(parents=True,exist_ok=True,mode=0o700);root.chmod(0o700)
        self.path=root/'effects.sqlite3'
        db=self.connect()
        try:
            db.execute('CREATE TABLE IF NOT EXISTS effects(id TEXT PRIMARY KEY, fingerprint TEXT, request TEXT, status TEXT, result TEXT)')
            db.commit()
        finally:db.close()
        self.path.chmod(0o600)

    def connect(self):
        return sqlite3.connect(self.path,timeout=10)

    def completed(self, operation_id, request):
        db=self.connect()
        try:
            row=db.execute('SELECT request,status,result FROM effects WHERE id=?',(operation_id,)).fetchone()
            if row and row[0]!=json.dumps(request,sort_keys=True):raise ValueError('Action receipt changed')
            return json.loads(row[2]) if row and row[1]=='done' else None
        finally:db.close()

    def pending(self, prefix):
        db=self.connect()
        try:
            values=[]
            for id,request in db.execute("SELECT id,request FROM effects WHERE status='started' ORDER BY rowid DESC LIMIT 100"):
                request=json.loads(request)
                if request.get('kind','').startswith(prefix):values.append((id,request))
            return values
        finally:db.close()

    def resolve(self, operation_id, request, result):
        db=self.connect()
        try:
            with db:
                row=db.execute('SELECT request FROM effects WHERE id=?',(operation_id,)).fetchone()
                if row is None or row[0]!=json.dumps(request,sort_keys=True):raise ValueError('Unknown action receipt')
                db.execute("UPDATE effects SET status='done',result=? WHERE id=?",(json.dumps(result),operation_id))
            return result
        finally:db.close()

    def release_unsent(self, operation_id, request):
        """Archive and release a proven-unsent attempt under the adapter's lock.

        The caller must have caught an explicit local preflight rejection and
        verified that no outbound-write journal exists. Absence of a result or
        elapsed time is never sufficient proof. Not exposed as a model tool.
        """
        db=self.connect()
        try:
            with db:
                db.execute('BEGIN IMMEDIATE')
                row=db.execute('SELECT request,status FROM effects WHERE id=?',(operation_id,)).fetchone()
                if row is None or row!=(json.dumps(request,sort_keys=True),'started'):
                    raise ValueError('Only the matching unsent attempt may be released')
                db.execute('CREATE TABLE IF NOT EXISTS unsent_attempts(sequence INTEGER PRIMARY KEY, operation_id TEXT, request TEXT)')
                db.execute('INSERT INTO unsent_attempts(operation_id,request) VALUES (?,?)',(operation_id,row[0]))
                db.execute('DELETE FROM effects WHERE id=?',(operation_id,))
        finally:db.close()

    def run(self, operation_id, request, execute, reconcile=None):
        if not operation_id:raise ValueError('Host receipt required')
        encoded=json.dumps(request,sort_keys=True)
        fingerprint=hashlib.sha256(encoded.encode()).hexdigest()
        db=self.connect()
        try:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT request,status,result FROM effects WHERE id=?',(operation_id,)).fetchone()
            if row:
                if row[0]!=encoded:raise ValueError('Action receipt changed')
                if row[1]=='done':return json.loads(row[2])
                # Another process could still own an in-flight request. Only
                # read-only reconciliation is allowed; never repeat the write.
                db.commit()
                result=reconcile() if reconcile is not None else None
                if result is None:raise UncertainEffect('Previous action outcome is unconfirmed')
            else:
                # A new Slack event must not evade an unresolved identical action.
                pending=db.execute("SELECT id FROM effects WHERE fingerprint=? AND status='started'",(fingerprint,)).fetchone()
                if pending:raise UncertainEffect('An identical action is still unconfirmed')
                db.execute('INSERT INTO effects VALUES (?,?,?,?,?)',(operation_id,fingerprint,encoded,'started',''))
                db.commit()
                # Crash/timeout/error leaves the durable started receipt intact.
                result=execute()
            serialized=json.dumps(result)
            db.execute("UPDATE effects SET status='done',result=? WHERE id=?",(serialized,operation_id))
            db.commit()
            return result
        finally:db.close()
