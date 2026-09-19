"""Slack digest scheduler: durable generation, one message, crash reconciliation."""
import fcntl
import hashlib
import html
import json
import os
import threading
import uuid
from datetime import datetime, timedelta, timezone

from .conversation import _write
from .message_format import plain_text, slack_text
from .digest import DigestStore, compose, identity, scope, slot
from .digest_sources import collect


def known_thread(home, config, thread):
    if not (home / 'digest' / 'digest.sqlite3').exists():return False
    db=DigestStore(home)
    try:return db.thread(scope(config),thread) is not None
    finally:db.close()


class DigestManager:
    def __init__(self, service):
        self.service=service
        self.db=DigestStore(service.store.home)
        self.owner=scope(service.config)
        self.workers={}

    def tick(self, now=None):
        now=now or datetime.now(timezone.utc)
        p=self.db.preferences(self.owner)
        for row in self.db.db.execute("SELECT data FROM runs WHERE scope=? AND status IN ('queued','building','ready')",(self.owner,)).fetchall():
            old=json.loads(row[0])
            if now.timestamp()>=old['deadline']:
                old['status']='expired';self.db.save(old,now)
        if not p['enabled']:return
        from .digest import allowed_day
        if not allowed_day(now,p):return
        day,due,deadline=slot(now,p)
        # Finish uncertain delivery receipts even after their morning window.
        rows=self.db.db.execute("SELECT data FROM runs WHERE scope=? AND status='sending'",(self.owner,)).fetchall()
        for row in rows:
            run=json.loads(row[0])
            if now.timestamp() >= run.get('retry_at',0):self.deliver(run,now)
        if not due-timedelta(minutes=5) <= now < deadline:return
        key=self.owner+':'+day
        run=self.db.get(key)
        if run is None:
            run=dict(key=key,scope=self.owner,day=day,status='queued',deadline=deadline.timestamp(),
                     identity=identity(self.service.config),attempts=0,retry_at=0,created=now.timestamp(),not_before=due.timestamp())
            run=self.db.create(run,now)
        self.advance(run,now)

    def preview(self, key, now=None):
        now=now or datetime.now(timezone.utc)
        p=self.db.preferences(self.owner)
        run=self.db.get(key)
        if run is None:
            run=dict(key=key,scope=self.owner,day=now.date().isoformat(),status='queued',
                     deadline=now.timestamp()+1800, identity=identity(self.service.config),
                     attempts=0,retry_at=0,created=now.timestamp(),preview=True)
            run=self.db.create(run,now)
        self.advance(run,now)
        return self.db.get(key)

    def advance(self,run,now):
        if run['status'] in ('sent','expired','failed','sending'):return
        if now.timestamp() >= run['deadline']:
            run['status']='expired';self.db.save(run,now);return
        if now.timestamp()<run.get('retry_at',0):return
        if run['status']=='ready':
            if now.timestamp() >= run.get('not_before',0):self.deliver(run,now)
            return
        worker=self.workers.get(run['key'])
        if worker and worker.is_alive():return
        directory=self.db.root/hashlib.sha256(run['key'].encode()).hexdigest()
        directory.mkdir(mode=0o700,exist_ok=True)
        fd=os.open(directory/'generation.lock',os.O_RDWR|os.O_CREAT,0o600)
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:os.close(fd);return
        run=self.db.get(run['key'])
        if run['status'] not in ('queued','building'):
            os.close(fd);return
        if run['attempts']>=3:
            run['status']='failed';self.db.save(run,now);os.close(fd);return
        run.update(status='building',attempts=run['attempts']+1)
        self.db.save(run,now)
        config=json.loads(json.dumps(self.service.config))
        objectives=self.service.store.list()
        p=self.db.preferences(self.owner);seen=self.db.history(self.owner)
        def work():
            db=DigestStore(self.service.store.home)
            try:
                attempt=directory/str(run['attempts']);attempt.mkdir(mode=0o700,exist_ok=True)
                evidence=collect(config,p,objectives,now,self.service.store.home)
                from .personalization import snapshot
                evidence['interest_context']=snapshot(self.service.store.home,config,directory)
                _write(attempt/'evidence.json',evidence)
                payload=compose(evidence,p,seen,attempt)
                from .digest_briefings import collect as collect_briefings
                briefings=collect_briefings(self.service.store.home,config,directory/'briefings',seen)
                payload['briefing_findings']=briefings['findings']
                if briefings['text']:payload['text']+='\n\n'+briefings['text']
                if len(payload['text'])>10000:raise ValueError('Digest too long')
                run.update(status='ready',payload=payload)
            except Exception:
                run.update(status='queued',retry_at=datetime.now(timezone.utc).timestamp()+60)
                _write(directory/'failure.json',{'reason':'generation_failed','attempt':run['attempts']})
            finally:
                db.save(run,datetime.now(timezone.utc));db.close();os.close(fd)
        thread=threading.Thread(target=work,daemon=True);self.workers[run['key']]=thread;thread.start()

    def reconcile(self,run):
        cursor=''
        for _ in range(10):
            result=self.service.client.conversations_history(channel=run['identity']['channel_id'],
                oldest=str(run['created']-60),limit=100,cursor=cursor,include_all_metadata=True)
            if not result.get('ok',True):raise ValueError('History unavailable')
            for message in result.get('messages',[]):
                marker=(message.get('metadata') or {}).get('event_payload',{}).get('key')
                if (message.get('bot_id') and message.get('user') == self.service.bot_user_id and (marker==run['marker'] or message.get('client_msg_id')==run['marker'])
                        and message.get('text')==run.get('wire_text', html.escape(run['payload']['text'],quote=False))):
                    return message['ts']
            cursor=result.get('response_metadata',{}).get('next_cursor','')
            if not cursor:return None
        raise ValueError('History scan incomplete')

    def deliver(self,run,now):
        lock=self.db.root/(hashlib.sha256(run['key'].encode()).hexdigest()+'.delivery.lock')
        fd=os.open(lock,os.O_RDWR|os.O_CREAT,0o600)
        notice_fd=None
        try:
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:return
            run=self.db.get(run['key'])
            if run['status'] not in ('ready','sending'):return
            if now.timestamp()<run.get('retry_at',0):return
            if run.get('payload',{}).get('task_notices'):
                notice_fd=os.open(self.service.store.home/'task-notice.delivery.lock',os.O_RDWR|os.O_CREAT,0o600)
                try:fcntl.flock(notice_fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
                except BlockingIOError:return
                from .attention import filter_notices
                try:
                    if not filter_notices(self,run,now):return
                except Exception:
                    run['retry_at']=now.timestamp()+60;self.db.save(run,now);return
            self._deliver(run,now)
        finally:
            if notice_fd is not None:os.close(notice_fd)
            os.close(fd)

    def _deliver(self,run,now):
        if now.timestamp() < run.get('not_before',0):return
        if run['status']=='sending':
            try:ts=self.reconcile(run)
            except Exception:
                run['retry_at']=now.timestamp()+60;self.db.save(run,now);return
            if ts:
                self.db.delivered(run,ts,now);return
            if now.timestamp()-run['sending_at']<60:return
        if now.timestamp()>=run['deadline']:
            run['status']='expired';self.db.save(run,now);return
        if 'wire_text' not in run:
            run['wire_text'] = slack_text(plain_text(run['payload']['text']))
            run['wire_mrkdwn'] = True
        run.update(status='sending',sending_at=now.timestamp(),retry_at=now.timestamp()+60,
                   marker=str(uuid.uuid5(uuid.NAMESPACE_URL,run['key'])))
        self.db.save(run,now)
        try:
            result=self.service.client.chat_postMessage(channel=run['identity']['channel_id'],
                text=run['wire_text'],client_msg_id=run['marker'],
                metadata={'event_type':'capo_digest','event_payload':{'key':run['marker']}},
                mrkdwn=run.get('wire_mrkdwn',False),parse='none',link_names=False,unfurl_links=False,unfurl_media=False)
            if not result.get('ok',True) or not result.get('ts'):return
            self.db.delivered(run,result['ts'],now)
        except Exception as exc:
            # Log only a bounded known error code, never a response body or token.
            response=getattr(exc,'response',None)
            code=response.get('error') if response is not None else None
            fatal={'invalid_auth','missing_scope','channel_not_found','not_in_channel','invalid_metadata','invalid_arguments','account_inactive'}
            run['delivery_error']=code if code in fatal|{'ratelimited'} else 'unconfirmed'
            if code in fatal:run['status']='failed'
            self.db.save(run,now)
            # An unconfirmed post may have succeeded; reconcile before retrying.
            return


def tick(service):
    if not hasattr(service,'digest_manager'):
        db=DigestStore(service.store.home)
        try:enabled=db.preferences(scope(service.config))['enabled']
        finally:db.close()
        if not enabled:return
        service.digest_manager=DigestManager(service)
    service.digest_manager.tick()
