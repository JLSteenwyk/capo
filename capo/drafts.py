"""Shared Gmail draft tools with verified recipients and MIME attachment references."""
import base64
import hashlib
import json
import re
from email import policy
from email.parser import BytesParser
from email.message import EmailMessage
from email.utils import getaddresses
from urllib.parse import quote

from .contracts import TEXT, TEXTS, object_schema
from .effects import Effects
from .research_tools import ReadTool


LIMIT = 5 * 1024 * 1024


def addresses(values):
    if len(values)>20 or any(not isinstance(v,str) or len(v)>300 or '\n' in v or '\r' in v for v in values):
        raise ValueError('Invalid recipients')
    result=[]
    for value in values:
        parsed=getaddresses([value])
        if len(parsed)!=1 or not re.fullmatch(r'[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+',parsed[0][1]):
            raise ValueError('Use a complete email address')
        address=parsed[0][1].casefold()
        if address in result:raise ValueError('Duplicate recipient')
        result.append(address)
    return result


class DraftTools:
    def __init__(self, client, mail, home, owner, request=None):
        self.client=client;self.mail=mail;self.effects=Effects(home,owner)
        self.known=set();self.attachments=mail.attachments.references;self.cursors={};self.actions={};self.reconcile_cursors={}
        request=request or {}
        owner_text=request.get('message','')+'\n'+'\n'.join(x.get('user','') for x in request.get('recent_messages',[]))
        self.explicit=set(re.findall(r'[^\s<>@,;]+@[^\s<>@,;]+\.[A-Za-z]{2,}',owner_text))
        self.explicit={x.casefold() for x in self.explicit}

    def search(self,query,cursor):
        if len(query)>500 or len(cursor)>2000 or (cursor and self.cursors.get(cursor)!=query):
            raise ValueError('Invalid draft search')
        params={'q':query,'maxResults':50}
        if cursor:params['pageToken']=cursor
        page=self.client.get('drafts',params)
        rows=[{'id':r['id'],'message_id':r['message']['id']} for r in page.get('drafts',[])[:50]]
        self.known.update(r['id'] for r in rows)
        token=page.get('nextPageToken','')
        if token:self.cursors[token]=query
        return {'drafts':rows,'cursor':token,'coverage':'One page of up to 50 draft identifiers; read before editing.'}

    def _raw(self,id):
        if id not in self.known:raise ValueError('Find the draft first')
        data=self.client.get('drafts/'+quote(id,safe=''),{'format':'raw'})
        encoded=data['message']['raw']
        if len(encoded)>LIMIT*2:raise ValueError('Draft too large')
        raw=base64.urlsafe_b64decode(encoded+'='*(-len(encoded)%4))
        if len(raw)>LIMIT:raise ValueError('Draft too large')
        return data,raw,BytesParser(policy=policy.default).parsebytes(raw)

    def read(self,id):
        data,raw,message=self._raw(id)
        revision=hashlib.sha256(raw).hexdigest()
        attachments=[]
        for index,part in enumerate(message.walk()):
            if part.get_content_disposition()=='attachment' or part.get_filename():
                content=part.get_payload(decode=True) or b''
                key=hashlib.sha256((id+revision+str(index)).encode()).hexdigest()
                self.attachments[key]=(part.get_filename() or 'attachment',part.get_content_type(),content)
                attachments.append({'id':key,'filename':part.get_filename(),'type':part.get_content_type(),'bytes':len(content)})
        body=message.get_body(preferencelist=('plain','html'))
        text=body.get_content() if body is not None else ''
        return {'id':id,'revision':revision,'thread_id':data['message'].get('threadId',''),
                'to':message.get('To',''),'cc':message.get('Cc',''),'bcc':message.get('Bcc',''),
                'subject':message.get('Subject',''),'body':str(text)[:12000],
                'truncated':len(str(text))>12000,'attachments':attachments}

    def source_attachments(self,message_id):
        return self.mail.attachments.source(message_id)

    def _parent(self,id):
        if id not in self.mail.known_ids:raise ValueError('Search/read the reply message first')
        value=self.client.get('messages/'+quote(id,safe=''),{'format':'metadata'})
        headers={h['name'].lower():h['value'] for h in value.get('payload',{}).get('headers',[])}
        return value,headers

    def save(self,id,revision,to,cc,bcc,subject,body,reply_to_message,attachment_ids,operation_id):
        request=dict(kind='gmail-draft-save',id=id,revision=revision,to=to,cc=cc,bcc=bcc,subject=subject,
                     body=body,reply_to_message=reply_to_message,attachment_ids=attachment_ids)
        existing=self.effects.completed(operation_id,request)
        if existing is not None:return existing
        self.client.ready()
        if len(body)>12000 or not body.strip() or len(subject)>300 or '\r' in subject or '\n' in subject:
            raise ValueError('Invalid draft body or subject')
        if len(attachment_ids)>10 or len(set(attachment_ids))!=len(attachment_ids):raise ValueError('Invalid attachments')
        recipients=addresses(to+cc+bcc)
        if not to:raise ValueError('A draft needs a recipient')
        trusted=set(self.explicit);parent=None;old_message=None;old_data=None
        if id:
            old_data,raw,old_message=self._raw(id)
            if hashlib.sha256(raw).hexdigest()!=revision:raise ValueError('Draft changed; read again before editing')
            trusted.update(a.casefold() for _,a in getaddresses([str(old_message[k]) for k in ('To','Cc','Bcc') if old_message.get(k)]))
        elif revision:raise ValueError('New drafts do not have revisions')
        if reply_to_message:
            parent,headers=self._parent(reply_to_message)
            trusted.update(a.casefold() for _,a in getaddresses([headers[k] for k in ('reply-to','from','to','cc') if headers.get(k)]))
            if subject!=headers.get('subject',''):raise ValueError('Keep the original subject to preserve the reply thread')
        if not set(recipients)<=trusted:
            raise ValueError('Recipient not verified from owner text or source correspondence')
        message=EmailMessage()
        message['To']=', '.join(to)
        if cc:message['Cc']=', '.join(cc)
        if bcc:message['Bcc']=', '.join(bcc)
        message['Subject']=subject
        tag=hashlib.sha256(operation_id.encode()).hexdigest()
        message['Message-ID']='<capo-'+tag+'@capo.invalid>'
        message['X-Capo-Action']='capo-'+tag+'@capo.invalid'
        thread_id=''
        if parent:
            reference=headers.get('message-id','')
            if not reference or '\r' in reference or '\n' in reference:raise ValueError('Reply lacks a valid message ID')
            message['In-Reply-To']=reference
            message['References']=(headers.get('references','')+' '+reference).strip()
            thread_id=parent['threadId']
        elif old_message is not None:
            for key in ('In-Reply-To','References'):
                if old_message.get(key):message[key]=str(old_message[key])
            thread_id=old_data['message'].get('threadId','')
        message.set_content(body)
        for key in attachment_ids:
            if key not in self.attachments:raise ValueError('Read the source attachment first')
            name,mime,content=self.attachments[key]
            main,sub=mime.split('/',1)
            message.add_attachment(content,maintype=main,subtype=sub,filename=name)
        raw=message.as_bytes()
        if len(raw)>LIMIT:raise ValueError('Draft exceeds attachment size limit')
        payload={'message':{'raw':base64.urlsafe_b64encode(raw).decode()}}
        if thread_id:payload['message']['threadId']=thread_id
        def write():
            result=self.client.draft_write('PUT' if id else 'POST',id,payload)
            self.known.add(result['id'])
            return {'saved':True,'id':result['id'],'thread_id':result.get('message',{}).get('threadId',''),
                    'to':to,'cc':cc,'bcc':bcc,'subject':subject,'attachments':len(attachment_ids),'sent':False}
        return self.effects.run(operation_id,request,write)

    def delete(self,id,revision,operation_id):
        request={'kind':'gmail-draft-delete','id':id,'revision':revision}
        existing=self.effects.completed(operation_id,request)
        if existing is not None:return existing
        _,raw,_=self._raw(id)
        if hashlib.sha256(raw).hexdigest()!=revision:raise ValueError('Draft changed; read again before deleting')
        return self.effects.run(operation_id,{'kind':'gmail-draft-delete','id':id,'revision':revision},
                                lambda:self.client.draft_write('DELETE',id))

    def pending(self):
        result=[]
        for operation,request in self.effects.pending('gmail-draft-'):
            key=hashlib.sha256(operation.encode()).hexdigest()
            self.actions[key]=(operation,request)
            result.append({'id':key,'operation':request['kind'],'draft_id':request.get('id',''),
                           'subject':request.get('subject',''),'status':'unconfirmed'})
        return {'actions':result,'coverage':'Up to 100 recent unconfirmed actions. Reconcile before retrying a write.'}

    def reconcile(self,id,cursor=''):
        if id not in self.actions:raise ValueError('List pending actions first')
        if len(cursor)>2000 or (cursor and self.reconcile_cursors.get(cursor)!=id):raise ValueError('Use the returned cursor for this action')
        operation,request=self.actions[id]
        completed=self.effects.completed(operation,request)
        if completed is not None:return completed
        if request['kind']=='gmail-draft-delete':
            if self.client.draft_exists(request['id']):return {'confirmed':False,'reason':'Draft still exists; no change was made.'}
            return self.effects.resolve(operation,request,{'deleted':True,'id':request['id'],'reconciled':True})
        tag='capo-'+hashlib.sha256(operation.encode()).hexdigest()+'@capo.invalid'
        next_cursor=''
        if request['id']:ids=[request['id']]
        else:
            params={'maxResults':10}
            if cursor:params['pageToken']=cursor
            page=self.client.get('drafts',params)
            ids=[v['id'] for v in page.get('drafts',[])[:10]]
            next_cursor=page.get('nextPageToken','')
            if next_cursor:self.reconcile_cursors[next_cursor]=id
        for draft_id in ids:
            self.known.add(draft_id)
            try:
                metadata=self.client.get('drafts/'+quote(draft_id,safe=''),{'format':'metadata'})
                headers={h['name'].lower():h['value'] for h in metadata['message'].get('payload',{}).get('headers',[])}
                if headers.get('x-capo-action')!=tag and headers.get('message-id')!='<'+tag+'>':continue
                data,raw,message=self._raw(draft_id)
            except Exception:continue
            if str(message.get('X-Capo-Action',''))!=tag and str(message.get('Message-ID',''))!='<'+tag+'>':continue
            body=message.get_body(preferencelist=('plain',))
            changed=body is None or body.get_content().strip()!=request['body'].strip() or str(message.get('Subject',''))!=request['subject']
            return self.effects.resolve(operation,request,{'saved':True,'id':draft_id,'sent':False,
                'reconciled':True,'changed_since_write':changed,'thread_id':data['message'].get('threadId','')})
        return {'confirmed':False,'cursor':next_cursor,'reason':'No matching draft in this page. Follow the returned cursor if present; otherwise the outcome remains unconfirmed. No write was repeated.'}

    def tools(self):
        return [
            ReadTool('mail.drafts.pending','List unconfirmed draft changes after a timeout or interruption. Do this before retrying a failed draft write.',object_schema({}),self.pending),
            ReadTool('mail.drafts.reconcile','Check a pending action ID against Gmail without repeating the write. Uses a preserved action header or an explicit not-found response after deletion. Start with empty cursor; follow returned cursors for this action. Checks at most ten draft headers per call; missing evidence stays unconfirmed.',object_schema({'id':TEXT,'cursor':TEXT}),self.reconcile),
            ReadTool('mail.drafts.search','Find Gmail drafts using Gmail search syntax. Empty query finds all; use returned cursor for more.',object_schema({'query':TEXT,'cursor':TEXT}),self.search),
            ReadTool('mail.drafts.read','Read a discovered draft, current revision, recipients and attachment references before editing or deleting.',object_schema({'id':TEXT}),self.read),
            ReadTool('mail.drafts.save','Create or update an owner-requested Gmail draft; never sends it. New: empty id/revision. Edit: latest revision and full replacement content. Recipients must appear in owner text or source correspondence. For replies supply a searched message ID and preserve its subject exactly. Preserve existing attachments by their read reference IDs unless owner asks to remove them. Use the approved writing guide.',object_schema({'id':TEXT,'revision':TEXT,'to':TEXTS,'cc':TEXTS,'bcc':TEXTS,'subject':TEXT,'body':TEXT,'reply_to_message':TEXT,'attachment_ids':TEXTS}),self.save,mutates=True),
            ReadTool('mail.drafts.delete','Delete an owner-requested draft after reading its current revision; never deletes sent or received email.',object_schema({'id':TEXT,'revision':TEXT}),self.delete,mutates=True),
        ]
