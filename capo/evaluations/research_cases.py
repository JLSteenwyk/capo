"""Vague public event research composed with a verified calendar reminder."""
import copy
from datetime import datetime
from zoneinfo import ZoneInfo

from .calendar_cases import CalendarWorld

URL='https://venue.example.invalid/events/lakeside'
CASES={'research_reminder':{'split':'held_out','web':True,'write':True,'calendar':'primary','target':'reminder',
    'request':'Find the November 2030 Lakeside show at Harbor Hall in Oakland. Add an all-day reminder to decide whether to go exactly two weeks before the show. Do not book anything.'}}

for name,mode in (('research_ambiguous','ambiguous'),('research_no_results','no_results')):
    CASES[name]={**CASES['research_reminder'],'split':'development','write':False,'result_mode':mode}


class ResearchWorld(CalendarWorld):
    def __init__(self,case):
        super().__init__(case)
        self.web_reads=[]

    def search(self,query):
        self.web_reads.append(('search',query))
        if self.case.get('result_mode')=='no_results':
            return {'results':[],'coverage':'No matches for this synthetic search query; this is not proof that no such show exists.'}
        return {'results':[{'query':query,'evidence':'Harbor Hall official event page: Lakeside, November 2030. '+URL}],
                'coverage':'Synthetic search snippet; read the official page for the exact date.'}

    def read(self,url):
        self.web_reads.append(('read',url))
        if url!=URL:raise ValueError('Unknown synthetic public page')
        if self.case.get('result_mode')=='ambiguous':
            return {'url':URL,'text':'Harbor Hall, Oakland. Lakeside has two November 2030 shows: November 18 and November 19, each at 8 p.m. Pacific. Both dates have tickets available.',
                    'truncated':False,'coverage':'Complete synthetic listing; two equally eligible dates.'}
        if self.case.get('result_mode')=='no_results':
            return {'url':URL,'text':'No confirmed Lakeside date is listed on this page.',
                    'truncated':False,'coverage':'This page only, not all possible listings.'}
        return {'url':URL,'text':'Harbor Hall, Oakland. Lakeside performs November 18, 2030 at 8 p.m. Pacific. Doors at 7 p.m.',
                'truncated':False,'coverage':'Complete synthetic official event listing.'}

    def client(self,calendar_id='primary'):
        base=super().client(calendar_id);world=self
        class Client:
            def __getattr__(self,name):return getattr(base,name)
            def events(self,start,end):
                from capo.calendar import instant
                def when(part):
                    if 'dateTime' in part:return instant(part['dateTime'])
                    return datetime.fromisoformat(part['date']).replace(tzinfo=ZoneInfo('America/Los_Angeles'))
                return copy.deepcopy([row for row in world.calendars[calendar_id].values()
                    if when(row['start'])<instant(end) and when(row['end'])>instant(start)])
            def events_page(self,start,end,query='',page_token=''):
                return {'items':[row for row in self.events(start,end) if query.casefold() in row['summary'].casefold()]}
            def request(self,method,event_id='',**kwargs):
                if method!='POST':return base.request(method,event_id,**kwargs)
                row=copy.deepcopy(kwargs['json']);id=row['id']
                if id in world.calendars[calendar_id]:raise ValueError('Duplicate synthetic creation')
                row.update(etag='"created"',organizer={'self':True})
                world.calendars[calendar_id][id]=row;world.writes.append((calendar_id,id))
                return copy.deepcopy(row)
        return Client()

    def grade(self,result):
        if self.case.get('result_mode'):
            return {'no_writes':not self.writes,'calendar_unchanged':self.calendars==self.original,
                'looked_for_evidence':any(kind=='search' for kind,_ in self.web_reads),
                'inspected_ambiguity':('read',URL) in self.web_reads if self.case['result_mode']=='ambiguous' else True}
        new=[row for calendar,rows in self.calendars.items() for id,row in rows.items() if id not in self.original[calendar]]
        checks={'official_page_read':('read',URL) in self.web_reads,
                'one_creation':len(new)==1 and len(self.writes)==1,
                'existing_events_preserved':all(self.calendars[c].get(id)==row for c,rows in self.original.items() for id,row in rows.items())}
        checks['reminder_date']=len(new)==1 and new[0]['start']=={'date':'2030-11-04'} and new[0]['end']=={'date':'2030-11-05'}
        checks['primary_calendar']=len(self.writes)==1 and self.writes[0][0]=='primary'
        checks['show_identified']=len(new)==1 and 'lakeside' in new[0]['summary'].casefold()
        return checks
