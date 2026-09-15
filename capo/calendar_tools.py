"""Calendar discovery and inspection with resource identity and explicit coverage."""
import copy
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from .calendar import instant, writable
from .contracts import TEXT, TEXTS, object_schema
from .research_tools import ReadTool


def selection_settings(settings):
    preferred=settings.get('default_calendar_id','primary')
    calendars=settings.get('availability_calendar_ids',[preferred])
    def valid(value):
        return isinstance(value,str) and 0<len(value)<=500 and not any(ord(c)<32 for c in value)
    if not valid(preferred) or not isinstance(calendars,list) or not 1<=len(calendars)<=10 or not all(valid(v) for v in calendars) or len(set(calendars))!=len(calendars):
        raise ValueError('Configure a calendar ID and one to ten unique availability calendar IDs')
    return {'default_calendar_id':preferred,'availability_calendar_ids':list(calendars)}


class CalendarTools:
    def __init__(self, zone, client_factory=None, preferences=None):
        self.zone = zone
        self.preferences = selection_settings(preferences or {})
        self.factory = client_factory
        self.calendars = {'primary':{'id':'primary','primary':True}}
        self.cursors = {}
        self.cache = {}
        self.primary_cache = {}

    def canonical(self, calendar_id):
        if calendar_id not in self.calendars:
            raise ValueError('Discover the calendar first with calendar.calendars')
        return 'primary' if self.calendars[calendar_id].get('primary') else calendar_id

    def client(self, calendar_id):
        if calendar_id not in self.calendars:
            raise ValueError('Discover the calendar first with calendar.calendars')
        calendar_id = self.canonical(calendar_id)
        from .calendar import GoogleCalendar
        factory = self.factory or GoogleCalendar
        return factory() if calendar_id=='primary' else factory(calendar_id)

    def token(self, token, key):
        if len(token)>2000 or (token and self.cursors.get(token)!=key):
            raise ValueError('Cursor does not belong to this calendar query')

    def page(self, data, key):
        token = data.get('nextPageToken','')
        if token: self.cursors[token] = key
        return {'next_page_token':token,'more_available':bool(token)}

    def list(self, page_token):
        self.token(page_token, ('calendars',))
        data = self.client('primary').calendar_list(page_token)
        rows=[]
        for row in data.get('items',[])[:50]:
            value={key:row[key] for key in ('id','summary','description','timeZone','accessRole','primary','selected','hidden') if key in row}
            if 'description' in value:
                value['description_truncated']=len(value['description'])>12000
                value['description']=value['description'][:12000]
            self.calendars[row['id']]=value
            if row.get('primary'):
                self.calendars['primary']=dict(value,id='primary')
            rows.append(value)
        return {'calendars':rows, **self.page(data, ('calendars',)),
                'coverage':'Up to 50 calendars on this account’s calendar list. Access roles describe Google access, not additional Capo action authority.'}

    def inspect(self, calendar_id):
        calendar_id = self.canonical(calendar_id)
        data = self.client(calendar_id).calendar_info()
        if data.get('timeZone'):self.calendars[calendar_id]['timeZone']=data['timeZone']
        value={key:data[key] for key in ('id','summary','description','location','timeZone') if key in data}
        if 'description' in value:
            value['description_truncated']=len(value['description'])>12000
            value['description']=value['description'][:12000]
        value.update(calendar_id=calendar_id, access_role=self.calendars[calendar_id].get('accessRole','unknown'))
        return {'calendar':value,'coverage':'Metadata for the selected calendar; no events read.'}

    def remember(self, calendar_id, row):
        if row.get('id'):
            self.cache[(calendar_id,row['id'])]=copy.deepcopy(row)
            if calendar_id=='primary':self.primary_cache[row['id']]=copy.deepcopy(row)

    def describe(self, calendar_id, row, details=False):
        keys = ('id','summary','start','end','location','status','transparency','eventType')
        value=copy.deepcopy({key:row[key] for key in keys if key in row})
        value['calendar_id']=calendar_id
        value['source_times']=copy.deepcopy({key:row[key] for key in ('start','end') if key in row})
        from .date_tools import describe as date_facts
        value['local_dates']={}
        for key in ('start','end'):
            source=value.get(key,{})
            if source.get('dateTime') or source.get('date'):
                value['local_dates'][key]=date_facts(source.get('dateTime') or source['date'],self.zone)
                if key=='end':value['local_dates'][key]['exclusive']=True
            if value.get(key,{}).get('dateTime'):
                local=instant(value[key]['dateTime']).astimezone(ZoneInfo(self.zone))
                value[key]={'dateTime':local.isoformat(),'timeZone':self.zone}
        if details:
            for key in ('organizer','creator','recurrence','recurringEventId','originalStartTime','htmlLink','etag','attendeesOmitted','visibility','reminders'):
                if key in row:value[key]=copy.deepcopy(row[key])
            description=row.get('description','')
            value.update(description=description[:12000],description_truncated=len(description)>12000,
                attendees=copy.deepcopy(row.get('attendees',[])[:100]),
                attendees_truncated=len(row.get('attendees',[]))>100,
                editable_by_capo=writable(row),
                edit_policy='Only owner-requested personal events, organized by this account, without guests or recurrence; current revision checked before writes.')
        return value

    @staticmethod
    def window(start,end):
        first,last=instant(start),instant(end)
        if not 0<(last-first).total_seconds()<=31*86400:
            raise ValueError('Calendar range must be at most 31 days')

    def events(self, start, end):
        """Preserve the existing primary-calendar contract for saved callers."""
        self.window(start,end)
        client=self.client('primary')
        rows=client.events(start,end)
        if isinstance(getattr(client,'events_timezone',None),str):
            self.calendars['primary']['timeZone']=client.events_timezone
        for row in rows:self.remember('primary',row)
        return {'events':[self.describe('primary',row) for row in rows], 'coverage':'Primary calendar only. Use calendar.event for descriptions, attendees and edit restrictions.'}

    def search(self, calendar_id, start, end, query, page_token):
        calendar_id = self.canonical(calendar_id)
        self.window(start,end)
        if len(query)>500:raise ValueError('Calendar search is too long')
        key=(calendar_id,start,end,query)
        self.token(page_token,key)
        data=self.client(calendar_id).events_page(start,end,query,page_token)
        if isinstance(data.get('timeZone'),str):self.calendars[calendar_id]['timeZone']=data['timeZone']
        rows=[row for row in data.get('items',[])[:50] if row.get('status')!='cancelled']
        for row in rows:self.remember(calendar_id,row)
        return {'calendar_id':calendar_id,'events':[self.describe(calendar_id,row) for row in rows],
                **self.page(data,key),'coverage':'Up to 50 events from this calendar and window; not other calendars. Empty query finds all events. Inspect candidates before deciding.'}

    def event(self, calendar_id, event_id):
        calendar_id = self.canonical(calendar_id)
        if (calendar_id,event_id) not in self.cache:
            raise ValueError('Find the event in this calendar before inspecting it')
        row=self.client(calendar_id).lookup(event_id)
        if row is None:
            self.cache.pop((calendar_id,event_id),None)
            if calendar_id=='primary':self.primary_cache.pop(event_id,None)
            return {'found':False,'calendar_id':calendar_id,'event_id':event_id,'coverage':'The previously found event is now absent or cancelled.'}
        self.remember(calendar_id,row)
        return {'found':True,'event':self.describe(calendar_id,row,True),'coverage':'Current event details; description and attendee truncation are reported explicitly.'}

    def selection(self):
        return {**copy.deepcopy(self.preferences), 'timezone':self.zone,
                'coverage':'Owner-configured calendar choices. Discover and inspect calendars before using non-primary IDs. An explicit owner choice overrides the default; it grants no additional edit permission.'}

    def availability(self,start,end,work_start,work_end,weekdays,minimum_minutes,calendar_ids=None):
        from .availability import availability
        from .digest import wall
        from .recovery import failure_summary
        selected=self.preferences['availability_calendar_ids'] if calendar_ids is None else calendar_ids
        selection_settings({'availability_calendar_ids':selected})
        selected=list(dict.fromkeys(self.canonical(id) for id in selected))
        # Validate window and work-hour arguments before any external reads.
        availability([],start,end,self.zone,work_start,work_end,weekdays,minimum_minutes)
        events=[];coverage=[];references={}
        for calendar_id in selected:
            try:
                page=self.events(start,end) if calendar_id=='primary' else self.search(calendar_id,start,end,'','')
                batch=[]
                for original in page['events']:
                    row=copy.deepcopy(original)
                    for boundary in ('start','end'):
                        if row.get(boundary,{}).get('date'):
                            zone=self.calendars[calendar_id].get('timeZone',self.zone if calendar_id=='primary' else None)
                            if zone is None:raise ValueError('Inspect the calendar timezone before calculating all-day availability')
                            local=wall(datetime.fromisoformat(row[boundary]['date']).date(),'00:00',zone)
                            row[boundary]={'dateTime':local.isoformat()}
                    key=row['id'] if selected==['primary'] else json.dumps([calendar_id,row['id']])
                    references[key]={'calendar_id':calendar_id,'event_id':row['id']}
                    row['id']=key;batch.append(row)
                events.extend(batch)
                coverage.append({'calendar_id':calendar_id,'complete':not page.get('more_available',False),
                                 'next_page_token':page.get('next_page_token',''),
                                 'timezone_source':'calendar' if self.calendars[calendar_id].get('timeZone') else
                                     'configured_primary_default' if calendar_id=='primary' else 'not_reported'})
            except Exception as exc:
                coverage.append({'calendar_id':calendar_id,'complete':False,'error':failure_summary(exc)})
        result=availability(events,start,end,self.zone,work_start,work_end,weekdays,minimum_minutes)
        complete=all(row['complete'] for row in coverage) and not result['unreadable_event_ids']
        if not complete:result['free_windows']=[]
        for conflict in result['conflicts']:
            conflict['event_refs']=[references[id] for id in conflict['event_ids']]
        return {**result,'status':'complete' if complete else 'partial','calendars':coverage,
                'coverage':'Selected calendars only; work hours are supplied assumptions. No free windows are reported if any selected calendar is incomplete or unavailable. Travel and unrecorded commitments are not included; no time was booked.'}

    def action_tools(self, home, owner):
        from dataclasses import replace
        from .calendar_actions import CalendarActions
        actions = CalendarActions(home, owner, self.zone, self.primary_cache)
        tools = actions.tools()
        def change(calendar_id=None, **arguments):
            if calendar_id is None:
                calendar_id=self.preferences['default_calendar_id'] if arguments.get('action')=='create' else 'primary'
            calendar_id = self.canonical(calendar_id)
            if calendar_id != 'primary' and self.calendars[calendar_id].get('accessRole') != 'owner':
                raise PermissionError('Capo changes events only on calendars owned by this account')
            cache = self.primary_cache if calendar_id=='primary' else {
                event_id:row for (cal_id,event_id),row in self.cache.items() if cal_id==calendar_id}
            return CalendarActions(home, owner, self.zone, cache, calendar_id=calendar_id).change(**arguments)
        schema = copy.deepcopy(tools[0].arguments)
        # Optional for persisted callers; new requests can name a discovered calendar.
        schema['properties']['calendar_id'] = TEXT
        tools[0] = replace(tools[0], arguments=schema, execute=change,
            description=tools[0].description+' Optional calendar_id selects a discovered owned calendar. Creation defaults to the owner preference from calendar.preferences; legacy update/delete default to primary. Carry the calendar ID from inspection; never infer identity from event ID alone.')
        return tools

    def tools(self):
        return [
            ReadTool('calendar.preferences','Read owner-configured creation and availability calendar choices before selecting a calendar. Preferences do not grant access.',object_schema({}),self.selection),
            ReadTool('calendar.calendars','Discover calendars, names, timezones and access roles. Start with empty page_token. Requires Google calendar read permission.',object_schema({'page_token':TEXT}),self.list),
            ReadTool('calendar.inspect','Inspect metadata for primary or a discovered calendar ID.',object_schema({'calendar_id':TEXT}),self.inspect),
            ReadTool('calendar.events','Read primary-calendar events in a maximum 31-day explicit RFC3339 window. Use calendar.search for a selected calendar and calendar.event for details.',object_schema({'start':TEXT,'end':TEXT}),self.events),
            ReadTool('calendar.search','Find events in primary or a discovered calendar. Empty query means all events; empty page_token starts a search. Reuse the cursor only with the same calendar, window and query.',object_schema({'calendar_id':TEXT,'start':TEXT,'end':TEXT,'query':TEXT,'page_token':TEXT}),self.search),
            ReadTool('calendar.event','Inspect a discovered event with description, attendees, organizer, recurrence, timezone and edit restrictions. Calendar and event IDs must match the search result.',object_schema({'calendar_id':TEXT,'event_id':TEXT}),self.event),
        ]
