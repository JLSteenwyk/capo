"""Deliver task reminders through the existing durable Slack delivery gateway."""
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from .capabilities import owner_key
from .digest import DigestStore, identity, scope
from .digest_service import DigestManager
from .tasks import Tasks


class ReminderManager(DigestManager):
    def __init__(self, service):
        self.service=service
        self.db=DigestStore(service.store.home/'reminders')
        self.owner=scope(service.config)
        self.tasks=Tasks(service.store.home,owner_key(service.config))
        self.last_tick=0

    def tick(self,now=None):
        if now is None:
            if time.monotonic()-self.last_tick<15:return
            self.last_tick=time.monotonic()
            now=datetime.now(timezone.utc)
        tasks=[];cursor=''
        while True:
            page=self.tasks.search(status='active',cursor=cursor)
            tasks.extend(page['tasks']);cursor=page['cursor']
            if not cursor:break
        active={t['id']:t for t in tasks}
        # Reconcile uncertain posts even if the task was subsequently cancelled.
        for row in self.db.db.execute("SELECT data FROM runs WHERE scope=? AND status IN ('ready','sending','delivery_unknown')",(self.owner,)).fetchall():
            run=json.loads(row[0]);task=active.get(run['task_id'])
            stale=not task or task['remind_at']!=run['remind_at']
            if stale:
                if run['status']=='ready':run['status']='cancelled';self.db.save(run,now);continue
                run['deadline']=now.timestamp();self.db.save(run,now)
            self.deliver(run,now)
        for task in tasks:
            if not task['remind_at']:continue
            due=datetime.fromisoformat(task['remind_at'])
            if due>now:continue
            key=self.owner+':reminder:'+hashlib.sha256((task['id']+task['remind_at']).encode()).hexdigest()
            if self.db.get(key) is not None:continue
            late=(now-due).total_seconds()>300
            text=('Missed reminder: ' if late else 'Reminder: ')+task['title']
            if task['waiting_on']:text+=' (waiting on '+task['waiting_on'][:200]+')'
            text+='\nScheduled for '+due.strftime('%b %d, %I:%M %p')+' '+task['timezone']+'.'
            run=dict(key=key,scope=self.owner,day=now.date().isoformat(),status='ready',
                     created=now.timestamp(),deadline=now.timestamp()+86400,identity=identity(self.service.config),
                     task_id=task['id'],remind_at=task['remind_at'],payload={'text':text,'news':[]})
            run=self.db.create(run,now)
            self.deliver(run,now)


def known_thread(home,config,thread):
    return thread_context(home,config,thread) is not None


def thread_context(home,config,thread):
    path=Path(home)/'reminders/digest/digest.sqlite3'
    if not path.exists():return None
    db=DigestStore(Path(home)/'reminders')
    try:
        run=db.thread(scope(config),thread)
        if run:return {'task_id':run['task_id'],'reminder':run['payload']['text']}
        return None
    finally:db.close()


def tick(service):
    if not hasattr(service,'reminder_manager'):
        owner=hashlib.sha256(owner_key(service.config).encode()).hexdigest()
        if not (service.store.home/'tasks'/owner/'tasks.sqlite3').exists():return
        service.reminder_manager=ReminderManager(service)
    service.reminder_manager.tick()
