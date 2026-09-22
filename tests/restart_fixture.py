"""Synthetic durable remote services for actual process-death regression tests."""
import base64
import copy
import json
import os
import sys
from email import policy
from email.parser import BytesParser
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from capo.conversation import _write
from capo.calendar import event_body

PLAN=dict(action='create',event_id='',title='Workshop',location='Room A',start='2030-11-04',end='2030-11-05',all_day=True)
DRAFT=dict(id='',revision='',to=['friend@example.invalid'],cc=[],bcc=[],subject='Workshop',body='See you Monday.',reply_to_message='',attachment_ids=[])


class Remote:
    def __init__(self,root,crash=False):
        self.path=Path(root)/'remote.json';self.crash=crash
        if not self.path.exists():_write(self.path,dict(events={},drafts={},messages=[],writes=0))

    def state(self):return json.loads(self.path.read_text())

    def persist(self,state):
        state['writes']+=1;_write(self.path,state)
        if self.crash:os._exit(73)

    def events(self,*args):return list(self.state()['events'].values())
    def lookup(self,id):return self.state()['events'].get(id)

    def request(self,method,event_id='',**kwargs):
        state=self.state()
        if method=='GET':return copy.deepcopy(state['events'][event_id])
        if method=='POST':
            row=copy.deepcopy(kwargs['json']);state['events'][row['id']]=row
        elif method=='PATCH':
            row=state['events'][event_id]
            for key,value in kwargs['json'].items():
                if isinstance(value,dict):
                    row.setdefault(key,{})
                    for field,item in value.items():
                        if item is None:row[key].pop(field,None)
                        else:row[key][field]=item
                else:row[key]=value
        elif method=='DELETE':row=state['events'].pop(event_id)
        else:raise AssertionError(method)
        self.persist(state);return row

    def ready(self):return self
    def draft_exists(self,id):return id in self.state()['drafts']

    def draft_write(self,method,id='',payload=None):
        state=self.state();id=id or 'draft-one'
        if method=='DELETE':state['drafts'].pop(id)
        else:state['drafts'][id]={'id':id,'message':dict(payload['message'],threadId='thread-one')}
        self.persist(state);return {'id':id}

    def get(self,path,params):
        state=self.state()
        if path=='drafts':return {'drafts':[{'id':id,'message':{'id':'message-one'}} for id in state['drafts']]}
        row=state['drafts'][path.split('/')[1]]
        if params.get('format')=='metadata':
            m=BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(row['message']['raw']))
            return {'message':{'payload':{'headers':[{'name':k,'value':str(v)} for k,v in m.items()]}}}
        return row

    def chat_postMessage(self,**payload):
        state=self.state();row=dict(payload,ts='2.000',user='synthetic-bot',bot_id='synthetic-app')
        state['messages'].append(row);self.persist(state);return {'ok':True,'ts':row['ts']}

    def conversations_replies(self,**kwargs):return {'ok':True,'messages':self.state()['messages']}


def kill_after_write(root,kind):
    remote=Remote(root,crash=True);home=Path(root)/'capo'
    if kind.startswith('calendar'):
        from capo.calendar_actions import CalendarActions
        with patch('capo.calendar_actions.GoogleCalendar',return_value=remote):
            action=kind.split('-')[1];plan=dict(PLAN,action=action)
            if action!='create':
                state=remote.state();event=dict(event_body(PLAN,'America/Los_Angeles'),id='existing',etag='v1',organizer={'self':True},description='Preserve these notes.')
                state['events']['existing']=event;_write(remote.path,state)
                plan.update(event_id='existing',title='Updated workshop')
            adapter=CalendarActions(home,'owner','America/Los_Angeles',{e['id']:e for e in remote.events()})
            adapter.change(**plan,operation_id='original')
    elif kind=='draft':
        from capo.drafts import DraftTools
        from capo.gmail import GmailReadTools
        DraftTools(remote,GmailReadTools(remote),home,'owner',{'message':'Draft to friend@example.invalid'}).save(**DRAFT,operation_id='original')
    elif kind=='slack':
        from capo.slack_outbox import SlackOutbox
        SlackOutbox(home,remote,'synthetic-bot').post('reply-one',{'channel':'synthetic-channel','thread_ts':'1.000','text':'All requested details.'})
    else:raise AssertionError(kind)
    raise AssertionError('The fault did not kill the process')


if __name__=='__main__':kill_after_write(sys.argv[1],sys.argv[2])
