"""Incremental, durable Slack recovery, isolated from the interactive loop."""
import hashlib
import json
import threading
import time

from .conversation import _write
from .message_format import slack_timestamp
from .store import Store


class RecoveryScan:
    def __init__(self, home, config, client):
        self.home,self.config,self.client=home,config,client
        scope=':'.join(config[k] for k in ('team_id','channel_id','owner_user_id'))
        self.path=home/('slack-recovery-'+hashlib.sha256(scope.encode()).hexdigest()+'.json')

    def step(self, now=None):
        from .slack import ingest
        now=time.time() if now is None else now
        state=json.loads(self.path.read_text()) if self.path.exists() else {
            'bootstrap_oldest':now-86400,'history':{},'threads':{},'lane':'history'}
        store=Store(self.home)
        try:
            # Locally known threads remain discoverable regardless of root age.
            seen=set()
            for (data,) in store.db.execute('SELECT data FROM slack_inbox'):
                body=json.loads(data);event=body.get('event',{})
                if body.get('team_id')!=self.config['team_id'] or event.get('channel')!=self.config['channel_id']:continue
                if event.get('ts'):
                    seen.add(event['ts'])
                    state['threads'].setdefault(event.get('thread_ts',event['ts']),{'polled_at':0})
            history=state['history']
            indexed=state['threads']
            # Alternate lanes; one API page per step, cursors survive restarts.
            use_history=state['lane']=='history' or not indexed
            target=history if use_history else min(indexed.values(),key=lambda row:row.get('polled_at',0))
            root=None if use_history else next(k for k,v in indexed.items() if v is target)
            if not target.get('cursor'):
                target['upper']=now
                target['lower']=max(state['bootstrap_oldest'],target.get('watermark',state['bootstrap_oldest'])-60)
            args={'channel':self.config['channel_id'],'cursor':target.get('cursor',''),'limit':100,
                  'latest':slack_timestamp(target['upper'])}
            # Pin bootstrap boundaries and cursors before the network call so
            # a prolonged read outage cannot silently move the recovery floor.
            _write(self.path,state)
            if use_history:
                # Bootstrap history has no lower bound: discover old roots incrementally.
                if history.get('discovered'):
                    args['oldest']=slack_timestamp(target['lower'])
                response=self.client.conversations_history(**args)
            else:
                response=self.client.conversations_replies(ts=root,oldest=slack_timestamp(target['lower']),**args)
            if response.get('ok') is False:raise RuntimeError('Slack history read rejected')
            queued=0
            for message in response.get('messages',[]):
                ts=message.get('ts')
                if not ts:continue
                if use_history and (message.get('reply_count') or message.get('user')==self.config['owner_user_id']):
                    indexed.setdefault(ts,{'polled_at':0})
                if float(ts)<target['lower'] or ts in seen or message.get('user')!=self.config['owner_user_id'] or message.get('bot_id') or message.get('subtype') not in (None,'file_share'):continue
                event={k:message[k] for k in ('ts','text','user','thread_ts','files','subtype') if k in message}
                if root and ts!=root:event['thread_ts']=root
                event.update(type='message',channel=self.config['channel_id'],capo_recovered=True)
                # The durable owner inbox is the authority for whether this exact
                # request was received; unrelated bot text is never evidence.
                body={'team_id':self.config['team_id'],'event_id':'recovered:'+self.config['channel_id']+':'+ts,'event':event}
                queued+=bool(ingest(self.home,self.config,body));seen.add(ts)
            cursor=response.get('response_metadata',{}).get('next_cursor','')
            target['cursor']=cursor
            if not cursor:
                target.update(watermark=target['upper'],polled_at=now)
                if use_history:target['discovered']=True
            # Keep a partial thread at the front until its pagination completes.
            state['lane']='thread' if use_history else 'history'
            state.update(checked_at=now,queued_last_page=queued,coverage={
                'historical_roots_complete':bool(history.get('discovered')),
                'indexed_threads':len(indexed),
                'threads_not_yet_checked':sum(not v.get('watermark') for v in indexed.values()),
                'pagination_pending':bool(history.get('cursor')) or any(v.get('cursor') for v in indexed.values()),
                'bootstrap_oldest':state['bootstrap_oldest']})
            _write(self.path,state)
            return queued
        finally:store.db.close()


class BackgroundRecovery:
    def __init__(self,home,config,client,connected):
        self.scan=RecoveryScan(home,config,client);self.connected=connected
        self.stop=threading.Event();self.thread=None

    def run(self):
        while not self.stop.is_set():
            delay=5
            if self.connected():
                try:self.scan.step()
                except Exception:
                    delay=60
                    _write(self.scan.path.with_suffix('.error.json'),{'at':time.time(),'status':'read_failed','retry_seconds':delay})
            self.stop.wait(delay)

    def start(self):
        self.thread=threading.Thread(target=self.run,name='slack-history-recovery',daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:self.thread.join(timeout=5)
