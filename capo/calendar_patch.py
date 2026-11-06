"""Allowlisted calendar field patches with optimistic concurrency and receipts."""
import fcntl
import hashlib
import json
import os
from .calendar import GoogleCalendar
from .conversation import _write
from .effects import Effects,UncertainEffect
from .research_tools import ToolInputError

FIELDS={'summary':1000,'description':12000,'location':2000}
PRESERVE=('start','end','attendees','recurrence','recurringEventId','reminders','attachments','conferenceData','visibility','transparency','extendedProperties')


class CalendarPatch:
    def __init__(self,home,owner,calendar_id,cache,*,effects=None):
        self.effects=effects if effects is not None else Effects(home,owner);self.calendar_id=calendar_id;self.cache=cache

    def client(self):
        return GoogleCalendar() if self.calendar_id=='primary' else GoogleCalendar(self.calendar_id)

    def snapshot(self,op):
        return self.effects.path.parent/('patch-'+hashlib.sha256(op.encode()).hexdigest()+'.json')

    def reconcile(self,op,request):
        event=self.client().lookup(request['plan']['event_id'])
        path=self.snapshot(op)
        if not event or event.get('status')=='cancelled' or not path.exists():return None
        snapshot=json.loads(path.read_text())
        expected={**snapshot,**request['plan']['fields']}
        if event.get('organizer',{}).get('self') is not True or any(event.get(k,'')!=v for k,v in expected.items()):return None
        return self.effects.resolve(op,request,{'verified':True,'changed':True,'event_id':event['id'],
            'calendar_id':self.calendar_id,'reply':'Calendar details updated and verified.'})

    def patch(self,event_id,fields,scope,notify_guests,operation_id):
        if not isinstance(fields,dict) or not fields or set(fields)-set(FIELDS):raise ToolInputError('Patch summary, description or location only.')
        for key,value in fields.items():
            if not isinstance(value,str) or len(value)>FIELDS[key] or '\x00' in value:raise ToolInputError('Invalid calendar field value.')
        if 'summary' in fields and not fields['summary'].strip():raise ToolInputError('Event title cannot be empty.')
        if scope not in ('event','instance','series') or type(notify_guests) is not bool:raise ToolInputError('Choose event, instance or series scope and an explicit notification policy.')
        plan=dict(action='patch',title='',event_id=event_id,calendar_id=self.calendar_id,fields=fields,scope=scope,notify_guests=notify_guests)
        request={'kind':'calendar-patch','plan':plan}
        fd=os.open(self.effects.path.parent/'calendar-change.lock',os.O_CREAT|os.O_RDWR,0o600)
        try:
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise UncertainEffect('Another calendar change is running.') from None
            saved=self.effects.completed(operation_id,request)
            if saved is not None:return saved
            for op,req in self.effects.pending('calendar-'):
                if req==request:
                    result=self.reconcile(op,req)
                    if result:return result
                    raise UncertainEffect('The previous patch remains unconfirmed; no write repeated.')
                if req['plan'].get('event_id')==event_id and req['plan'].get('calendar_id','primary')==self.calendar_id:
                    raise UncertainEffect('Reconcile the earlier calendar action before another patch.')
            old=self.cache.get(event_id)
            if not old:raise ToolInputError('Inspect with calendar.event before patching.')
            client=self.client();current=client.lookup(event_id)
            if (not current or current.get('status')=='cancelled' or current.get('attendeesOmitted')
                or current.get('organizer',{}).get('self') is not True or current.get('eventType','default')!='default'):
                raise ToolInputError('Patch a fully readable event organized by this account.')
            actual='instance' if current.get('recurringEventId') else 'series' if current.get('recurrence') else 'event'
            if scope!=actual:raise ToolInputError('Scope differs from the inspected event; inspect the correct instance or series.')
            if not current.get('etag') or current['etag']!=old.get('etag'):raise ToolInputError('Event changed; inspect again.')
            if all(current.get(k,'')==v for k,v in fields.items()):return {'verified':True,'changed':False,'reply':'The requested details are already present; no write sent.'}
            preserved={k:current[k] for k in PRESERVE if k in current}
            preserved.update({k:current.get(k,'') for k in FIELDS if k not in fields})
            _write(self.snapshot(operation_id),preserved)
            def execute():
                client.request('PATCH',event_id,json=fields,headers={'If-Match':current['etag']},
                               params={'sendUpdates':'all' if notify_guests else 'none'})
                result=self.reconcile(operation_id,request)
                if result is None:raise UncertainEffect('Patch sent but not verified; reconcile before another write.')
                return result
            return self.effects.run(operation_id,request,execute,lambda:self.reconcile(operation_id,request))
        finally:os.close(fd)
