"""Additive invitations with ETag protection and no replay of uncertain sends."""
import fcntl
import os
from .calendar import GoogleCalendar
from .effects import Effects, UncertainEffect
from .research_tools import ToolInputError


def confirmed(client, plan):
    event=client.lookup(plan['event_id'])
    if not event or event.get('status')=='cancelled' or event.get('attendeesOmitted'):return False
    return event.get('organizer',{}).get('self') is True and set(plan['emails'])<={a.get('email','').lower() for a in event.get('attendees',[])}


class Invitations:
    def __init__(self,home,owner,calendar_id,cache):
        self.effects=Effects(home,owner);self.calendar_id=calendar_id;self.cache=cache

    def reconcile(self, operation_id, request):
        client=GoogleCalendar() if self.calendar_id=='primary' else GoogleCalendar(self.calendar_id)
        if not confirmed(client,request['plan']):return None
        return self.effects.resolve(operation_id,request,{'verified':True,'reconciled':True,
            'event_id':request['plan']['event_id'],'calendar_id':self.calendar_id,
            'reply':'The requested guests are on the event. No invitation was resent.'})

    def invite(self,event_id,emails,operation_id):
        fd=os.open(self.effects.path.parent/'calendar-change.lock',os.O_CREAT|os.O_RDWR,0o600)
        try:
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise UncertainEffect('Another calendar change is running; check its result first.') from None
            return self._invite(event_id,emails,operation_id)
        finally:os.close(fd)

    def _invite(self,event_id,emails,operation_id):
        plan={'action':'invite','event_id':event_id,'calendar_id':self.calendar_id,'emails':sorted(set(emails)),'title':''}
        request={'kind':'calendar-invite','plan':plan}
        saved=self.effects.completed(operation_id,request)
        if saved is not None:return saved
        for prior,req in self.effects.pending('calendar-'):
            if req==request:
                result=self.reconcile(prior,req)
                if result:return result
                raise UncertainEffect('Guest addition remains unconfirmed. No invitation was repeated.')
            if req['plan']['event_id']==event_id and req['plan'].get('calendar_id','primary')==self.calendar_id:
                raise UncertainEffect('An earlier invitation for this event remains unconfirmed. Reconcile it first.')
        if event_id not in self.cache:raise ToolInputError('Inspect this event with calendar.event before inviting guests.')
        client=GoogleCalendar() if self.calendar_id=='primary' else GoogleCalendar(self.calendar_id)
        current=client.lookup(event_id)
        if (not current or current.get('status')=='cancelled' or current.get('attendeesOmitted')
                or current.get('organizer',{}).get('self') is not True or not current.get('etag')
                or current.get('eventType','default')!='default'):
            raise ToolInputError('Guests can only be added to a fully readable event organized by this account.')
        if current.get('recurrence') or current.get('recurringEventId'):
            raise ToolInputError('Adding guests to an existing recurring series or instance is not yet supported. New series can include contact_ids at creation.')
        if current['etag']!=self.cache[event_id].get('etag'):
            raise ToolInputError('The event changed. Inspect it again before adding guests.')
        old=current.get('attendees',[])
        additions=[{'email':address} for address in emails if address not in {a.get('email','').lower() for a in old}]
        if not additions:return {'changed':False,'verified':True,'event_id':event_id,'reply':'These guests are already on the event; no invitation was resent.'}
        if len(old)+len(additions)>100:raise ToolInputError('This event exceeds the supported guest limit.')
        def execute():
            client.request('PATCH',event_id,json={'attendees':old+additions},
                           headers={'If-Match':current['etag']},params={'sendUpdates':'all'})
            if not confirmed(client,plan):raise UncertainEffect('Guest update was sent but could not be verified. Do not resend.')
            return {'changed':True,'verified':True,'event_id':event_id,'calendar_id':self.calendar_id,
                    'reply':'Guests added; Google was asked to send invitations. Email delivery and acceptance are not confirmed.'}
        return self.effects.run(operation_id,request,execute,lambda:self.reconcile(operation_id,request))
