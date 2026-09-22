"""Durable reply chunks; ambiguous sends are reconciled, never blindly repeated."""
import fcntl
import json
import os
import time
import uuid

from .conversation import _write


class SlackOutbox:
    def __init__(self,home,client,bot_user_id=None):
        self.root=home/'slack-outbox';self.root.mkdir(mode=0o700,parents=True,exist_ok=True)
        self.client=client;self.bot_user_id=bot_user_id

    def reconcile(self,state):
        if not self.bot_user_id:return None
        payload=state['payload'];cursor=''
        for _ in range(5):
            page=self.client.conversations_replies(channel=payload['channel'],ts=payload['thread_ts'],
                oldest=str(state['started_at']-1),inclusive=True,limit=100,cursor=cursor,include_all_metadata=True)
            if not page.get('ok',True):return None
            for row in page.get('messages',[]):
                key=(row.get('metadata') or {}).get('event_payload',{}).get('key')
                if (row.get('bot_id') and row.get('user')==self.bot_user_id
                        and (key==state['marker'] or row.get('client_msg_id')==state['marker'])
                        and row.get('text')==payload['text'] and row.get('ts')
                        and row.get('thread_ts',payload['thread_ts'])==payload['thread_ts']):
                    return row['ts']
            cursor=page.get('response_metadata',{}).get('next_cursor','')
            if not cursor:return None
        return None  # Bounded or empty history does not prove a send failed.

    def post(self,key,payload):
        marker=str(uuid.uuid5(uuid.NAMESPACE_URL,json.dumps([key,payload],sort_keys=True)))
        path=self.root/(marker+'.json')
        fd=os.open(self.root/(marker+'.lock'),os.O_RDWR|os.O_CREAT,0o600)
        try:
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:return False
            state=json.loads(path.read_text()) if path.exists() else dict(marker=marker,payload=payload,status='ready')
            if state['payload']!=payload:raise ValueError('Slack delivery receipt changed')
            if state['status']=='sent':return True
            if time.time()<state.get('retry_at',0):return False
            if state['status']=='sending':
                try:ts=self.reconcile(state)
                except Exception:ts=None
                if ts:
                    state.update(status='sent',ts=ts);_write(path,state);return True
                state.update(retry_at=time.time()+60,error='Delivery remains unconfirmed; no message was repeated.')
                _write(path,state);return False
            state.update(status='sending',started_at=time.time())
            _write(path,state)
            try:
                response=self.client.chat_postMessage(**payload,client_msg_id=marker,
                    metadata={'event_type':'capo_reply','event_payload':{'key':marker}})
                if response.get('ok',True) and response.get('ts'):
                    state.update(status='sent',ts=response['ts']);_write(path,state);return True
                state['error']='Slack did not confirm delivery.'
                # An explicit API rejection establishes that this attempt was not accepted.
                if response.get('ok') is False:state['status']='ready'
                _write(path,state);return False
            except Exception as exc:
                response=getattr(exc,'response',None)
                status=getattr(response,'status_code',None)
                code=response.get('error') if callable(getattr(response,'get',None)) else None
                rejected=code in ('ratelimited','invalid_auth','missing_scope','channel_not_found','not_in_channel','invalid_arguments','invalid_metadata','account_inactive') or status==429
                if rejected:state['status']='ready'
                state['error']='Slack rejected delivery.' if rejected else 'Slack delivery is unconfirmed.'
                _write(path,state)
                if rejected:raise
                return False
        finally:os.close(fd)


def pending(home):
    root=home/'slack-outbox'
    rows=[]
    for path in root.glob('*.json'):
        state=json.loads(path.read_text())
        if state['status']=='sending':rows.append({'id':path.stem,'status':'unconfirmed',
            'started_at':state['started_at'],'next_action':'Read Slack history to reconcile; do not resend blindly.'})
    return rows[:20]
