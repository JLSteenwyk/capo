"""Owner-scoped private address book, shared across capabilities."""
import hashlib
import json
import re
import sqlite3
from pathlib import Path
from contextlib import closing

from .contracts import TEXT, object_schema
from .research_tools import ReadTool, ToolInputError


def email(value):
    value=value.strip().lower()
    if len(value)>254 or not re.fullmatch(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,63}",value):
        raise ToolInputError('Provide a complete email address supplied or verified by the owner; never guess one.')
    local,domain=value.split('@')
    if local.startswith('.') or local.endswith('.') or '..' in local or any(not label or label.startswith('-') or label.endswith('-') for label in domain.split('.')):
        raise ToolInputError('Provide a valid email address verified by the owner.')
    return value


class Contacts:
    def __init__(self, home, owner):
        root=Path(home)/'contacts'/hashlib.sha256(owner.encode()).hexdigest()
        root.mkdir(parents=True,exist_ok=True,mode=0o700);root.chmod(0o700)
        self.path=root/'contacts.sqlite3'
        with closing(sqlite3.connect(self.path)) as db,db:
            db.execute('CREATE TABLE IF NOT EXISTS contacts(id TEXT PRIMARY KEY,name TEXT,email TEXT UNIQUE)')
            db.execute('CREATE TABLE IF NOT EXISTS operations(id TEXT PRIMARY KEY,request TEXT,result TEXT)')
        self.path.chmod(0o600)

    def search(self, query):
        if len(query)>200:raise ToolInputError('Use a shorter contact name or email.')
        with closing(sqlite3.connect(self.path)) as db:
            db.create_function('casefold',1,lambda value:value.casefold())
            rows=db.execute("SELECT id,name,email FROM contacts WHERE instr(casefold(name || ' ' || email),?)>0 ORDER BY name,email LIMIT 51",(query.casefold(),)).fetchall()
        found=[dict(zip(('id','name','email'),r)) for r in rows]
        return {'contacts':found[:50],'more_available':len(found)>50,
                'coverage':'Private saved contacts only. Multiple matches require choosing the right person/address with the owner; never guess.'}

    def save(self, name, email_address, operation_id):
        name=name.strip();address=email(email_address)
        if not name or len(name)>200 or any(ord(c)<32 for c in name):raise ToolInputError('Provide a name under 200 characters.')
        id=hashlib.sha256(address.encode()).hexdigest()
        result={'saved':True,'contact':{'id':id,'name':name,'email':address}}
        return self.write(operation_id,['save',name,address],result,'INSERT INTO contacts VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name',(id,name,address))

    def remove(self, contact_id, operation_id):
        return self.write(operation_id,['remove',contact_id],{'removed':True,'coverage':'Address book entry removed; existing calendar invitations are unchanged.'},'DELETE FROM contacts WHERE id=?',(contact_id,))

    def write(self, operation_id, request, result, sql, arguments):
        encoded=json.dumps(request)
        with closing(sqlite3.connect(self.path)) as db,db:
            db.execute('BEGIN IMMEDIATE')
            prior=db.execute('SELECT request,result FROM operations WHERE id=?',(operation_id,)).fetchone()
            if prior:
                if prior[0]!=encoded:raise ToolInputError('Operation ID was already used for a different contact change.')
                return json.loads(prior[1])
            db.execute(sql,arguments)
            db.execute('INSERT INTO operations VALUES (?,?,?)',(operation_id,encoded,json.dumps(result)))
        return result

    def resolve(self, ids):
        if not isinstance(ids,list) or not 1<=len(ids)<=20 or len(set(ids))!=len(ids):
            raise ToolInputError('Choose one to twenty distinct saved contact IDs.')
        with closing(sqlite3.connect(self.path)) as db:
            rows=[db.execute('SELECT email FROM contacts WHERE id=?',(id,)).fetchone() for id in ids]
        if any(row is None for row in rows):raise ToolInputError('A contact was not found. Search or save the verified address first.')
        return sorted(email(row[0]) for row in rows)

    def tools(self):
        return [ReadTool('contacts.search','Find saved names and email addresses; empty query lists up to 50. Resolve ambiguous names with the owner before inviting. Contacts grant no permission to send messages.',object_schema({'query':TEXT}),self.search),
            ReadTool('contacts.save','Save a name and an email explicitly supplied or verified by the owner. Never infer an address. Same email updates its name; another email creates a separate entry. To correct an email, remove the old entry and save the replacement.',object_schema({'name':TEXT,'email_address':TEXT}),self.save,mutates=True),
            ReadTool('contacts.remove','Remove an owner-requested contact by ID. Does not modify events or notify anyone.',object_schema({'contact_id':TEXT}),self.remove,mutates=True)]
