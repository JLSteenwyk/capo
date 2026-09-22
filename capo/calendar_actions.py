"""Shared calendar mutations with duplicate checks and read-only recovery."""
import fcntl
import os
import hashlib
import json
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from .calendar import GoogleCalendar, apply, event_body, instant, writable, CalendarPreconditionFailed
from .contracts import TEXT, object_schema
from .effects import Effects, UncertainEffect
from .research_tools import ReadTool, ToolInputError
from .conversation import _write


def equivalent(event, body):
    if event.get('status')=='cancelled':return False
    if event.get('summary','').strip().casefold()!=body['summary'].strip().casefold():return False
    if event.get('location','').strip()!=body.get('location','').strip():return False
    for key in ('start','end'):
        left,right=event.get(key,{}),body[key]
        if 'date' in right:
            if left.get('dateTime') is not None:return False
            if left.get('date')!=right['date']:return False
        else:
            if left.get('date') is not None:return False
            try:
                if instant(left['dateTime'])!=instant(right['dateTime']):return False
            except (KeyError,ValueError):return False
    return True


class CalendarActions:
    def __init__(self,home,owner,zone,cache,calendar_id='primary'):
        self.home=Path(home);self.zone=zone;self.cache=cache;self.calendar_id=calendar_id
        self.effects=Effects(home,owner);self.known_pending={}

    def directory(self,operation_id):
        return self.home/'calendar-tools'/hashlib.sha256(operation_id.encode()).hexdigest()

    def target_id(self,operation_id,plan):
        return hashlib.sha256(str(self.directory(operation_id)).encode()).hexdigest() if plan['action']=='create' else plan['event_id']

    def verified(self,operation_id,request,client):
        plan=request['plan'];target=self.target_id(operation_id,plan)
        current=client.lookup(target)
        if plan['action']=='delete':
            return current is None
        elif current is None or not equivalent(current,event_body(plan,self.zone)):
            return False
        if current.get('id')!=target:return False
        expected=event_body(plan,self.zone)
        if current.get('summary')!=expected['summary'] or current.get('location','')!=expected['location']:
            return False
        snapshot=self.directory(operation_id)/'verification.json'
        if snapshot.exists():
            preserved=json.loads(snapshot.read_text())['preserved']
            if any(current.get(key)!=value for key,value in preserved.items()):return False
        return True

    def check_pending(self,operation_id,request,client):
        if not self.verified(operation_id,request,client):return None
        plan=request['plan'];target=self.target_id(operation_id,plan)
        return self.effects.resolve(operation_id,request,{'changed':True,'event_id':target,
                                    'reconciled':True,'verified':True,'calendar_id':plan.get('calendar_id','primary'),'reply':'Confirmed the requested calendar state.'})

    def pending(self):
        self.known_pending={hashlib.sha256(op.encode()).hexdigest():(op,req)
                            for op,req in self.effects.pending('calendar-change')}
        return {'actions':[{'id':id,'action':req['plan']['action'],'title':req['plan']['title'],'calendar_id':req['plan'].get('calendar_id','primary')}
                           for id,(_,req) in self.known_pending.items()]}

    def reconcile(self,id):
        if id not in self.known_pending:raise ValueError('List pending calendar actions first')
        op,req=self.known_pending[id]
        calendar_id=req['plan'].get('calendar_id','primary')
        client=GoogleCalendar() if calendar_id=='primary' else GoogleCalendar(calendar_id)
        result=self.check_pending(op,req,client)
        return result or {'confirmed':False,'reply':'The previous calendar change remains unconfirmed. No write was repeated.'}

    def change(self,action,event_id,title,location,start,end,all_day,operation_id):
        lock=self.effects.path.parent/'calendar-change.lock'
        fd=os.open(lock,os.O_CREAT|os.O_RDWR,0o600)
        try:
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise UncertainEffect('Another calendar change is running. Check its result before retrying.') from None
            return self._change(action,event_id,title,location,start,end,all_day,operation_id)
        finally:os.close(fd)

    def _change(self,action,event_id,title,location,start,end,all_day,operation_id):
        plan=dict(action=action,event_id=event_id,title=title,location=location,start=start,end=end,all_day=all_day,reply='')
        if self.calendar_id!='primary':plan['calendar_id']=self.calendar_id
        request={'kind':'calendar-change','plan':plan}
        previous=self.effects.completed(operation_id,request)
        if previous is not None:return previous
        body=event_body(plan,self.zone) if action in ('create','update') else None
        client=GoogleCalendar() if self.calendar_id=='primary' else GoogleCalendar(self.calendar_id)  # Authentication failures must precede the durable write intent.
        for prior,req in self.effects.pending('calendar-change'):
            if req==request:
                found=self.check_pending(prior,req,client)
                if found:return found
                raise UncertainEffect('Previous calendar change remains unconfirmed; no write repeated')
        if action in ('update','delete') and (event_id not in self.cache or not writable(self.cache[event_id])):
            raise ToolInputError('Inspect the event with calendar.event. This adapter cannot edit events with guests, recurrence, or another organizer. Do not repeat the same change while this restriction remains.')
        existing=None
        if action=='create':
            first,last=start,end
            if all_day:
                first=datetime.combine(datetime.fromisoformat(start).date(),time.min,ZoneInfo(self.zone)).isoformat()
                last=datetime.combine(datetime.fromisoformat(end).date(),time.min,ZoneInfo(self.zone)).isoformat()
            matches=[e for e in client.events(first,last) if equivalent(e,body)]
            if matches:
                existing={'changed':False,'already_exists':True,'event_id':matches[0]['id'],'calendar_id':self.calendar_id,
                          'reply':'The matching event is already on your calendar.'}
        directory=self.directory(operation_id);directory.mkdir(parents=True,exist_ok=True,mode=0o700)
        if action=='update':
            original=self.cache[event_id]
            keys=('description','attendees','recurrence','recurringEventId','attachments','reminders',
                  'visibility','transparency','extendedProperties','conferenceData')
            _write(directory/'verification.json',{'preserved':{key:original[key] for key in keys if key in original}})
        def execute():
            if existing is not None:return existing
            reply=apply(client,plan,list(self.cache.values()),self.zone,directory)
            if not self.verified(operation_id,request,client):
                raise UncertainEffect('The calendar write was sent, but the requested state could not be verified. Reconcile before another write.')
            return {'changed':True,'verified':True,'event_id':self.target_id(operation_id,plan),'calendar_id':self.calendar_id,'reply':reply}
        try:
            return self.effects.run(operation_id,request,execute)
        except CalendarPreconditionFailed:
            # This exception is emitted only before apply creates its write
            # journal. Retain any old/uncertain attempt rather than infer safety.
            if not (directory/'effect.json').exists():
                self.effects.release_unsent(operation_id,request)
                self.cache.pop(event_id,None)
            raise

    def tools(self):
        return [ReadTool('calendar.change','Apply an explicitly owner-requested personal change. Reads existing events before creation to avoid exact duplicates; read events before update/delete. Full preserved title/location/times on update. No guests/repeats. Timed values require local offsets; all-day end is exclusive. Unused strings empty. Unconfirmed identical changes are reconciled without another write.',
            object_schema({'action':{'type':'string','enum':['create','update','delete']},'event_id':TEXT,'title':TEXT,'location':TEXT,'start':TEXT,'end':TEXT,'all_day':{'type':'boolean'}}),self.change,mutates=True),
            ReadTool('calendar.pending','List uncertain calendar changes for read-only reconciliation.',object_schema({}),self.pending),
            ReadTool('calendar.reconcile','Check a listed uncertain action against the actual calendar. Never repeats a write; absence or conflicting evidence for a save remains unconfirmed.',object_schema({'id':TEXT}),self.reconcile,verifies=True)]
