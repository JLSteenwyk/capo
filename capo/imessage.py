"""Owner-only iMessage/Slack bridge using the existing canonical Capo queue."""
import hashlib
import html
import json
import math
import re
import time
import uuid
from pathlib import Path

from .bluebubbles import BlueBubbles, BridgeError
from .channel_sync import Journal


def address(value):
    value=value.strip().casefold()
    return re.sub(r'[ ()-]','',value) if '@' not in value else value


def settings(config):
    value=config.get('imessage',{})
    if not isinstance(value,dict) or type(value.get('enabled',False)) is not bool:
        raise ValueError('imessage.enabled must be true or false')
    if not value.get('enabled'):return None
    if not all(isinstance(value.get(k),str) and value[k].strip() for k in ('server_url','chat_guid','owner_address')):
        raise ValueError('Configure the iMessage server_url, chat_guid and owner_address privately')
    if not value['chat_guid'].startswith('iMessage;-;'):
        raise ValueError('Only a direct iMessage chat is supported')
    return value


def journal(home,config):
    from .capabilities import owner_key
    p=settings(config)
    if p is None:return None
    j=Journal(home,owner_key(config))
    j.bind({k:p[k] for k in ('server_url','chat_guid','owner_address')})
    return j


class Bridge:
    def __init__(self, home, config, blue, slack):
        self.home,self.config,self.blue,self.slack=Path(home),config,blue,slack
        self.p=settings(config)
        if self.p is None:raise ValueError('iMessage is disabled')
        self.j=journal(home,config)

    def check(self):
        chat=self.blue.chat(self.p['chat_guid'])
        participants=chat.get('participants',[])
        if (chat.get('guid')!=self.p['chat_guid'] or len(participants)!=1 or
                address(participants[0].get('address',''))!=address(self.p['owner_address'])):
            raise ValueError('The configured iMessage chat does not contain exactly the linked owner')
        identity=self.slack.auth_test()
        if identity.get('team_id')!=self.config['team_id']:
            raise ValueError('Slack authentication belongs to a different workspace')
        return {'direct_owner_chat':True,'slack_workspace':True,'private_api_required':False}

    def load(self,id):
        with self.j.connect() as db:
            row=db.execute('SELECT data FROM incoming WHERE id=?',(id,)).fetchone()
        return json.loads(row[0]) if row else None

    def save(self,id,value):
        with self.j.connect() as db:
            db.execute('INSERT OR REPLACE INTO incoming VALUES (?,?)',(id,json.dumps(value)))

    def owner_message(self,message):
        return (message.get('isFromMe') is False and not message.get('associatedMessageType') and
                address((message.get('handle') or {}).get('address',''))==address(self.p['owner_address']) and
                any(c.get('guid')==self.p['chat_guid'] for c in message.get('chats',[])))

    def incoming(self,message):
        if not self.owner_message(message):return
        guid=message.get('guid')
        if not isinstance(guid,str) or not guid or len(guid)>500:return
        id='imessage:'+hashlib.sha256(guid.encode()).hexdigest()
        data=self.load(id)
        if data and data.get('done'):return
        if data is None:
            stamp=message.get('dateCreated')
            if type(stamp) not in (int,float) or not math.isfinite(stamp) or stamp<=0:
                raise BridgeError('The message has no valid source timestamp')
            text=message.get('text') or ''
            if not isinstance(text,str) or len(text)>7500:raise BridgeError('iMessage text exceeds the request limit')
            thread=self.j.get('active_thread')
            if text.strip().casefold() == 'threads':
                self.thread_list(id)
                self.save(id,{'done':True});return
            match=re.match(r'^\[?(C[0-9A-Fa-f]{10})\]?:\s*(.*)$',text,re.S)
            if match:
                thread=self.j.thread(match[1]);text=match[2]
                if thread is None:raise BridgeError('Unknown conversation label')
            elif text.casefold().startswith('new:'):
                thread=None;text=text[4:].lstrip()
            elif text.casefold().startswith('switch '):
                thread=self.j.thread(text[7:].strip())
                if thread is None:raise BridgeError('Unknown conversation label')
                self.j.put('active_thread',thread)
                self.j.enqueue(id+':switch',thread,'Capo: Continuing this conversation.')
                self.save(id,{'done':True});return
            attachments=message.get('attachments') or []
            if len(attachments)>4:raise BridgeError('Send at most four images per request')
            if not text and not attachments:return
            data={'text':text or 'Please interpret the attached image.', 'thread':thread,'guid':guid,
                  'date_ms':message.get('dateCreated'), 'attachments':attachments,'files':[]}
            self.save(id,data)
        # Every outbound side effect records intent first. An uncertain mirror or
        # upload is left for inspection, never blindly repeated after a restart.
        if not data.get('slack_ts'):
            if data.get('mirror_started'):raise BridgeError('The Slack mirror is unconfirmed; inspect bridge status before retrying')
            data['mirror_started']=True;self.save(id,data)
            params={'channel':self.config['channel_id'],'text':'You (iMessage): '+html.escape(data['text'],quote=False),
                    'mrkdwn':False,'parse':'none','client_msg_id':str(uuid.uuid5(uuid.NAMESPACE_URL,id))}
            if data['thread']:params['thread_ts']=data['thread']
            result=self.slack.chat_postMessage(**params)
            if not result.get('ts'):raise BridgeError('Slack did not confirm the mirrored message')
            data['slack_ts']=result['ts'];data['thread']=data['thread'] or result['ts']
            self.save(id,data)
        self.j.enqueue(id+':ack',data['thread'],'Capo: Working on it…')
        try:self.deliver()
        except Exception:pass  # A failed acknowledgment must not block the owner's task.
        for i,item in enumerate(data['attachments']):
            if len(data['files'])>i:continue
            if data.get('upload_started'):raise BridgeError('An image upload is unconfirmed; inspect bridge status before retrying')
            raw=self.blue.attachment(item['guid'])
            from .slack_images import image_type
            mime=image_type(raw)
            data['upload_started']=True;self.save(id,data)
            uploaded=self.slack.files_upload_v2(content=raw,filename='image.'+mime.split('/')[1],
                channel=self.config['channel_id'],thread_ts=data['thread'])
            files=uploaded.get('files',[])
            if len(files)!=1 or not files[0].get('id'):raise BridgeError('Image upload was not confirmed')
            data['files'].append({'id':files[0]['id'],'mimetype':mime,'file_access':'check_file_info'})
            data.pop('upload_started',None);self.save(id,data)
        thread=data['thread'];self.j.put('active_thread',thread)
        event={'type':'app_mention','channel':self.config['channel_id'],'user':self.config['owner_user_id'],
               'ts':str(float(data['date_ms'])/1000),'thread_ts':thread,'text':data['text'],'files':data['files']}
        # This is trusted transport translation after exact account/chat checks,
        # not an event supplied by the model or a public webhook.
        from .slack import ingest
        body={'team_id':self.config['team_id'],'event_id':id,'event':event,
              'capo_transport':{'kind':'imessage','source_guid':guid,'sent_at_ms':data['date_ms']}}
        from .store import Store
        store=Store(self.home)
        try:already=store.db.execute('SELECT 1 FROM slack_inbox WHERE id=?',(id,)).fetchone()
        finally:store.db.close()
        if not already and not ingest(self.home,self.config,body):raise BridgeError('Canonical queue rejected the linked owner request')
        data['done']=True;self.save(id,data)

    def thread_list(self, id):
        from .store import Store
        from .slack import owner_message
        store=Store(self.home);items=[];seen=set()
        try:
            for row in store.db.execute('SELECT data FROM slack_inbox ORDER BY rowid DESC'):
                body=json.loads(row[0]);event=body.get('event',{})
                if not owner_message(self.config,body):continue
                thread=event.get('thread_ts',event['ts'])
                if thread in seen:continue
                seen.add(thread)
                title=re.sub(r'^\s*<@[A-Z0-9]+>[\s,:]*','',event.get('text','')).replace('\n',' ')[:100]
                items.append(self.j.code(thread)+': '+title)
                if len(items)==10:break
        finally:store.db.close()
        active=self.j.get('active_thread') or (next(iter(seen)) if seen else 'help')
        self.j.enqueue(id+':threads',active,'Capo: '+('Recent conversations:\n'+'\n'.join(items)+
            '\nSend switch CODE to continue one, or new: followed by your request.' if items else
            'Send a request to begin, or use new: to start a separate conversation.'))

    def collect_slack(self):
        from .store import Store
        from .slack import owner_message
        store=Store(self.home)
        try:
            cursor=self.j.get('slack_cursor')
            if cursor is None:
                cursor=store.db.execute('SELECT coalesce(max(rowid),0) FROM slack_inbox').fetchone()[0]
                self.j.put('slack_cursor',cursor)
            rows=store.db.execute('SELECT rowid,id,data FROM slack_inbox WHERE rowid>? ORDER BY rowid LIMIT 100',(cursor,)).fetchall()
            for row in rows:
                body=json.loads(row['data']);event=body.get('event',{})
                if owner_message(self.config,body) and not row['id'].startswith('imessage:'):
                    thread=event.get('thread_ts',event['ts']);self.j.put('active_thread',thread)
                    text=re.sub(r'^\s*<@[A-Z0-9]+>[\s,:]*','',event.get('text',''))
                    for f in event.get('files',[]):
                        if f.get('permalink'):text+='\nAttachment: '+f['permalink']
                        else:text+='\n[Attachment available in Slack]'
                    self.j.enqueue('slack-owner:'+row['id'],thread,'You (Slack): '+text)
                self.j.put('slack_cursor',row['rowid'])
        finally:store.db.close()

    def deliver(self):
        for row in self.j.pending():
            if row['state']=='sending':
                # tempGuid is only a live send-cache key, not durable idempotency.
                # Reconcile the exact rendered text in the bound chat instead.
                matches=[]
                for offset in range(0,1000,100):
                    batch=self.blue.messages(self.p['chat_guid'],int(row['started']*1000)-1000,offset)
                    matches += [m for m in batch if m.get('isFromMe') is True and m.get('text')==row['text']]
                    if len(batch)<100:break
                if len(matches)==1:self.j.sent(row['id'],matches[0]['guid']);continue
                raise BridgeError('An iMessage delivery is unconfirmed; no duplicate was sent')
            self.j.begin(row['id'])
            result=self.blue.send(self.p['chat_guid'],row['text'],str(uuid.uuid5(uuid.NAMESPACE_URL,row['id'])))
            if not isinstance(result,dict) or not result.get('guid'):raise BridgeError('iMessage send was not confirmed')
            self.j.sent(row['id'],result['guid'])
            if row['thread']!='help' and self.j.get('active_thread') is None:
                self.j.put('active_thread',row['thread'])

    def tick(self):
        self.check()
        self.collect_slack()
        with self.j.connect() as db:
            imports=[(row['id'],json.loads(row['data'])) for row in db.execute('SELECT id,data FROM incoming')]
        for id, data in imports:
            if data.get('done') or data.get('mirror_started') and not data.get('slack_ts') or data.get('upload_started'):
                continue
            # Successfully mirrored input can finish queue insertion after crash.
            if data.get('slack_ts'):
                self.incoming({'guid':data['guid'],'text':data['text'],'dateCreated':data['date_ms'],
                    'isFromMe':False,'handle':{'address':self.p['owner_address']},'chats':[{'guid':self.p['chat_guid']}]})
        after=self.j.get('imessage_after')
        if after is None:
            after=int(time.time()*1000);self.j.put('imessage_after',after)
        newest=after
        for offset in range(0,2000,100):
            batch=self.blue.messages(self.p['chat_guid'],after,offset)
            for message in batch:
                try:self.incoming(message)
                except Exception:
                    guid=message.get('guid','')
                    if self.owner_message(message) and guid:
                        id='imessage:'+hashlib.sha256(guid.encode()).hexdigest()
                        data=self.load(id)
                        thread=(data or {}).get('thread') or self.j.get('active_thread') or 'help'
                        self.j.enqueue(id+':failure',thread,'Capo: I could not confirm receiving that request. Please check imessage-status on the Mac before retrying.')
                        self.j.put('last_input_error',{'at':time.time(),'id':id})
                newest=max(newest,int(message.get('dateCreated') or after))
            if len(batch)<100:break
        if newest>after:self.j.put('imessage_after',newest-1)
        self.deliver()


def status(sync):
    with sync.connect() as db:
        states={r[0]:r[1] for r in db.execute('SELECT state,count(*) FROM outgoing GROUP BY state')}
        pending=sum(not json.loads(r[0]).get('done',False) for r in db.execute('SELECT data FROM incoming'))
    return {'health':sync.get('health'),'last_input_error':sync.get('last_input_error'),
            'deliveries':states,'unfinished_imports':pending}


def run(home,config,password_file,slack,check_only=False):
    if password_file.stat().st_mode & 0o077:raise ValueError('The BlueBubbles password file must have owner-only permissions')
    p=settings(config)
    if p is None:raise ValueError('Enable iMessage in the private configuration after linking its owner chat')
    bridge=Bridge(home,config,BlueBubbles(p['server_url'],password_file.read_text().strip()),slack)
    if check_only:return bridge.check()
    import fcntl
    with (bridge.j.path.parent/'imessage-service.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise ValueError('An iMessage bridge already owns this ledger') from None
        while True:
            try:
                bridge.tick();bridge.j.put('health',{'at':time.time(),'ok':True})
            except Exception:
                bridge.j.put('health',{'at':time.time(),'ok':False,'message':'Bridge needs attention; run imessage-check and inspect pending delivery state.'})
            time.sleep(5)
