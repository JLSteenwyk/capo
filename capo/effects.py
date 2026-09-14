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
