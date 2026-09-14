"""Run scheduled owner requests through the shared read-only capability loop."""
import fcntl
import hashlib
import json
import os
import threading
import time
from datetime import datetime,timezone
from pathlib import Path

from .capabilities import Documents,shared_tools,owner_key
from .conversation import _write
from .digest import DigestStore,identity,scope
from .digest_service import DigestManager
from .providers import Providers
from .research_tools import ReadTools,research
from .schedules import Schedules,due_slot


class ScheduledManager(DigestManager):
    def __init__(self,service):
        self.service=service;self.home=service.store.home/'scheduled'
        self.db=DigestStore(self.home);self.owner=scope(service.config)
        self.schedules=Schedules(service.store.home,owner_key(service.config))
        self.workers={};self.last_tick=0

    def tick(self,now=None):
        if now is None:
            if time.monotonic()-self.last_tick<15:return
            self.last_tick=time.monotonic();now=datetime.now(timezone.utc)
        schedules={s['id']:s for s in self.schedules.list()['schedules']}
        for row in self.db.db.execute("SELECT data FROM runs WHERE scope=? AND status IN ('queued','building')",(self.owner,)).fetchall():
            old=json.loads(row[0]);worker=self.workers.get(old['key'])
            if now.timestamp()>=old['deadline'] and not (worker and worker.is_alive()):
                old['status']='expired';self.db.save(old,now)
        for row in self.db.db.execute("SELECT data FROM runs WHERE scope=? AND status IN ('ready','sending')",(self.owner,)).fetchall():
            run=json.loads(row[0]);s=schedules.get(run['schedule_id'])
            if not s or not s['enabled'] or s['revision']!=run['revision']:
                if run['status']=='ready':run['status']='cancelled';self.db.save(run,now);continue
                run['deadline']=now.timestamp();self.db.save(run,now)
            self.deliver(run,now)
        for s in schedules.values():
            slot=due_slot(s,now)
            if slot is None:continue
            due,deadline=slot
            # Revisions don't create extra deliveries for the same scheduled occurrence.
            key=self.owner+':scheduled:'+s['id']+':'+due.isoformat()
            run=self.db.get(key)
            if run is None:
                run=self.db.create(dict(key=key,scope=self.owner,day=due.date().isoformat(),status='queued',
                    created=now.timestamp(),deadline=deadline.timestamp(),identity=identity(self.service.config),
                    schedule_id=s['id'],revision=s['revision'],request=s['request'],title=s['title'],attempts=0),now)
            if run['status'] not in ('queued','building') or now.timestamp()<run.get('retry_at',0):continue
            worker=self.workers.get(key)
            if worker and worker.is_alive():continue
            directory=self.db.root/hashlib.sha256(key.encode()).hexdigest();directory.mkdir(mode=0o700,exist_ok=True)
            fd=os.open(directory/'generation.lock',os.O_CREAT|os.O_RDWR,0o600)
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:os.close(fd);continue
            run=self.db.get(key)
            if run['status'] not in ('queued','building'):
                os.close(fd);continue
            if run['revision']!=s['revision']:
                run['status']='cancelled';self.db.save(run,now);os.close(fd);continue
            if run['attempts']>=2:
                run['status']='failed';self.db.save(run,now);os.close(fd);continue
            run.update(status='building',attempts=run['attempts']+1);self.db.save(run,now)
            config=json.loads(json.dumps(self.service.config))
            def work(run=run,s=s,directory=directory,fd=fd,config=config):
                db=None
                try:
                    db=DigestStore(self.home)
                    docs=Documents(self.service.store.home,owner_key(config))
                    request={'message':run['request'],'timezone':s['timezone']}
                    registry=shared_tools(self.service.store.home,config,docs,request)
                    readonly=ReadTools([t for t in registry.tools.values() if not t.mutates])
                    attempt=directory/'execution'
                    attempt.mkdir(parents=True,exist_ok=True,mode=0o700)
                    result=research(Providers(timeout=90),readonly,request,attempt,max_calls=10,recovery=config.get('recovery'),
                        instructions='This is an owner-scheduled read-only request. For planning, combine tasks, deadlines, waiting items, calendar availability and relevant email evidence. Identify preparation needs and conflicts; label assumptions about work hours and task durations. Report connection gaps. Never claim suggestions were booked or tasks changed. Give a concise usable plan.')
                    docs.save(directory,result);_write(attempt/'result.json',result)
                    run.update(status='ready',payload={'text':result['reply'],'news':[]})
                except Exception as exc:
                    from .recovery import RetryLater
                    if isinstance(exc, RetryLater):
                        run.update(status='queued', retry_at=exc.retry_at, attempts=max(0, run['attempts']-1))
                    else:
                        run.update(status='queued', retry_at=datetime.now(timezone.utc).timestamp()+60)
                finally:
                    try:
                        if db is not None:db.save(run,datetime.now(timezone.utc))
                    finally:
                        if db is not None:db.close()
                        os.close(fd)
            worker=threading.Thread(target=work,daemon=True);self.workers[key]=worker;worker.start()


def thread_context(home,config,thread):
    if not (Path(home)/'scheduled/digest/digest.sqlite3').exists():return None
    db=DigestStore(Path(home)/'scheduled')
    try:
        run=db.thread(scope(config),thread)
        return {'scheduled_request':run['request'],'result':run['payload']['text']} if run else None
    finally:db.close()


def tick(service):
    if not hasattr(service,'scheduled_manager'):
        owner=hashlib.sha256(owner_key(service.config).encode()).hexdigest()
        if not (service.store.home/'tasks'/owner/'tasks.sqlite3').exists():return
        service.scheduled_manager=ScheduledManager(service)
    service.scheduled_manager.tick()
