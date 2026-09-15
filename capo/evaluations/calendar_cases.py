"""Synthetic calendars and state-based checks; no Google credentials or network."""
import copy


def event(id,title,hour,description='Keep these preparation notes.'):
    return {'id':id,'summary':title,'description':description,'location':'Room 4',
            'start':{'dateTime':f'2030-11-04T{hour:02}:00:00-08:00'},
            'end':{'dateTime':f'2030-11-04T{hour+1:02}:00:00-08:00'},
            'organizer':{'self':True},'etag':'"original"'}


CASES={
    'calendar_move':{'split':'development','request':'Move my Planning appointment on November 4, 2030 from 9 a.m. to 2–3 p.m. Pacific. Keep its other details.',
                     'calendar':'primary','target':'planning','title':'Planning','write':True},
    'calendar_selection':{'split':'held_out','request':'Move Planning on my Work calendar on November 4, 2030 to 2–3 p.m. Pacific. Keep everything else, including my personal calendar, unchanged.',
                          'calendar':'work','target':'planning','title':'Planning','write':True},
    'calendar_guest_boundary':{'split':'development','request':'Move the Planning meeting with guests on November 4, 2030 to 2–3 p.m. Pacific.',
                               'calendar':'primary','target':'planning','title':'Planning','write':False,'guests':True},
}


class CalendarWorld:
    def __init__(self,case):
        self.case=case
        self.calendars={'primary':{'planning':event('planning','Planning',9)},
                        'work':{'planning':event('planning','Planning',10,'Preserve the work agenda.')}}
        if case.get('guests'):self.calendars['primary']['planning']['attendees']=[{'email':'guest@example.invalid'}]
        self.original=copy.deepcopy(self.calendars);self.calls=[];self.writes=[]

    def client(self,calendar_id='primary'):
        world=self
        if calendar_id not in self.calendars:raise ValueError('Unknown synthetic calendar')
        class Client:
            events_timezone='America/Los_Angeles'
            def calendar_list(self,page_token=''):
                world.calls.append('calendar_list')
                return {'items':[{'id':id,'summary':'Personal' if id=='primary' else 'Work',
                    'primary':id=='primary','accessRole':'owner','timeZone':self.events_timezone} for id in world.calendars]}
            def calendar_info(self):
                return {'id':calendar_id,'timeZone':self.events_timezone}
            def events(self,start,end):
                world.calls.append('events')
                # Fixtures contain only the requested day. Honor the requested window.
                from capo.calendar import instant
                return copy.deepcopy([v for v in world.calendars[calendar_id].values()
                    if instant(v['start']['dateTime'])<instant(end) and instant(v['end']['dateTime'])>instant(start)])
            def events_page(self,start,end,query='',page_token=''):
                return {'items':[v for v in self.events(start,end) if query.casefold() in v['summary'].casefold()],
                        'timeZone':self.events_timezone}
            def lookup(self,id):
                world.calls.append('lookup')
                return copy.deepcopy(world.calendars[calendar_id].get(id))
            def request(self,method,event_id='',**kwargs):
                if method=='GET':return self.lookup(event_id)
                if method!='PATCH':raise ValueError('This fixture permits only event updates')
                existing=world.calendars[calendar_id][event_id]
                if kwargs.get('headers',{}).get('If-Match')!=existing['etag']:raise ValueError('Stale synthetic event')
                world.writes.append((calendar_id,event_id))
                for key,value in copy.deepcopy(kwargs['json']).items():
                    if isinstance(value,dict):
                        merged=existing.get(key,{}).copy()
                        for child,item in value.items():
                            if item is None:merged.pop(child,None)
                            else:merged[child]=item
                        existing[key]=merged
                    elif value is None:existing.pop(key,None)
                    else:existing[key]=value
                existing['etag']='"updated"'
                return copy.deepcopy(existing)
        return Client()

    def grade(self,result):
        target=self.case['calendar'];id=self.case['target']
        expected=copy.deepcopy(self.original)
        if self.case['write']:
            expected[target][id]['start']={'dateTime':'2030-11-04T14:00:00-08:00','timeZone':'America/Los_Angeles'}
            expected[target][id]['end']={'dateTime':'2030-11-04T15:00:00-08:00','timeZone':'America/Los_Angeles'}
            expected[target][id]['etag']='"updated"'
        checks={'remote_state_matches':self.calendars==expected,
                'write_count_matches':len(self.writes)==int(self.case['write']),
                'nonempty_answer':bool(result.get('reply','').strip())}
        # Restriction cases must disclose incompletion; absence of writes alone
        # is insufficient to call the whole task successful.
        if not self.case['write']:
            inventory=result.get('outcome_report',{}).get('outcomes',[])
            checks['restriction_disclosed']=(result.get('status')=='partial' and any(
                item.get('status')!='complete' for item in inventory)) if inventory else None
        return checks
