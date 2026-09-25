"""Recover missing owner thread evidence without queuing historical actions."""
import hashlib
import json


class ThreadContextError(RuntimeError):
    pass


def recover(service, body):
    from .slack import authorized
    if not authorized(service.config, body, service.store):
        raise ValueError('Unauthorized thread request')
    event=body['event'];thread=event.get('thread_ts',event['ts'])
    if thread==event['ts']:return
    cache=getattr(service,'_recovered_thread_reads',{})
    read_key=(service.config['channel_id'],thread,event['ts'])
    if read_key in cache:return
    for row in service.store.db.execute('SELECT data FROM slack_inbox'):
        prior=json.loads(row[0])
        if authorized(service.config,prior,service.store) and prior['event']['ts']==thread:return
    # Read only this authorized conversation. Bound recovery; never enqueue old work.
    cursor=''
    recovered=[]
    try:
        for _ in range(3):
            for attempt in range(2):
                try:
                    page=service.client.conversations_replies(channel=service.config['channel_id'],
                        ts=thread,limit=100,**({'cursor':cursor} if cursor else {}))
                    break
                except (TimeoutError,ConnectionError):
                    if attempt:raise
            messages=page.get('messages',[])
            if not isinstance(messages,list):raise ValueError('Invalid Slack history response')
            for message in messages:
                if float(message.get('ts','0'))>=float(event['ts']):continue
                candidate={'team_id':service.config['team_id'],'event':dict(message,
                    type='message',channel=service.config['channel_id'])}
                other=candidate['event']
                if other.get('ts')!=thread and other.get('thread_ts')!=thread:continue
                if authorized(service.config,candidate,service.store):recovered.append(candidate)
            cursor=page.get('response_metadata',{}).get('next_cursor','')
            if not cursor:break
    except Exception as exc:
        raise ThreadContextError('I couldn’t retrieve the earlier messages from Slack. Please retry; I haven’t recovered the thread context yet.') from exc
    with service.store.db:
        for candidate in recovered:
            key='history:'+hashlib.sha256((service.config['channel_id']+':'+candidate['event']['ts']).encode()).hexdigest()
            service.store.db.execute('INSERT OR IGNORE INTO slack_inbox(id,data,handled) VALUES (?,?,1)',
                (key,json.dumps(candidate)))
    cache[read_key]=True
    if len(cache)>100:cache.pop(next(iter(cache)))
    service._recovered_thread_reads=cache
