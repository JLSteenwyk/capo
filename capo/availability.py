"""Calendar interval arithmetic reusable by planning and scheduling requests."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from .digest import wall


def availability(events, start, end, zone, work_start, work_end, weekdays, minimum_minutes):
    first=datetime.fromisoformat(start.replace('Z','+00:00'));last=datetime.fromisoformat(end.replace('Z','+00:00'))
    if first.tzinfo is None or last.tzinfo is None or not 0<(last-first).total_seconds()<=31*86400:
        raise ValueError('Use an explicit range of at most 31 days')
    first=first.astimezone(timezone.utc);last=last.astimezone(timezone.utc)
    if not weekdays or not set(weekdays)<=set('0123456') or len(set(weekdays))!=len(weekdays):
        raise ValueError('Weekdays are unique strings 0 (Monday) through 6 (Sunday)')
    for time in (work_start,work_end):
        if datetime.strptime(time,'%H:%M').strftime('%H:%M')!=time:raise ValueError('Use HH:MM')
    if work_start>=work_end:raise ValueError('Work window must end the same day after it starts')
    minimum=int(minimum_minutes)
    if not 1<=minimum<=1440:raise ValueError('Invalid minimum duration')
    def event_time(value):
        if value.get('dateTime'):
            t=datetime.fromisoformat(value['dateTime'].replace('Z','+00:00'))
            if t.tzinfo is None:raise ValueError('Calendar event lacks timezone')
            return t.astimezone(timezone.utc)
        return wall(datetime.fromisoformat(value['date']).date(),'00:00',zone).astimezone(timezone.utc)
    busy=[];conflicts=[];unavailable=[]
    for e in events:
        if e.get('status')=='cancelled' or e.get('transparency')=='transparent':continue
        try:a,b=event_time(e['start']),event_time(e['end'])
        except (KeyError,ValueError):unavailable.append(e.get('id','unknown'));continue
        a,b=max(a,first),min(b,last)
        if a<b:busy.append((a,b,e.get('id','unknown')))
    busy.sort()
    for i,(a,b,id) in enumerate(busy):
        for c,d,other in busy[i+1:]:
            if c>=b:break
            conflicts.append({'event_ids':[id,other],'start':c.astimezone(ZoneInfo(zone)).isoformat(),
                              'end':min(b,d).astimezone(ZoneInfo(zone)).isoformat()})
    windows=[];day=first.astimezone(ZoneInfo(zone)).date()
    while day<=last.astimezone(ZoneInfo(zone)).date():
        if str(day.weekday()) in weekdays:
            a=max(first,wall(day,work_start,zone).astimezone(timezone.utc))
            b=min(last,wall(day,work_end,zone).astimezone(timezone.utc));cursor=a
            for c,d,_ in busy:
                if d<=cursor or c>=b:continue
                if c>cursor and (c-cursor).total_seconds()>=minimum*60:
                    windows.append({'start':cursor.astimezone(ZoneInfo(zone)).isoformat(),'end':c.astimezone(ZoneInfo(zone)).isoformat(),'minutes':int((c-cursor).total_seconds()/60)})
                cursor=max(cursor,d)
            if b>cursor and (b-cursor).total_seconds()>=minimum*60:
                windows.append({'start':cursor.astimezone(ZoneInfo(zone)).isoformat(),'end':b.astimezone(ZoneInfo(zone)).isoformat(),'minutes':int((b-cursor).total_seconds()/60)})
        day+=timedelta(days=1)
    return {'free_windows':[] if unavailable else windows,'conflicts':conflicts,'unreadable_event_ids':unavailable,
            'coverage':'Primary calendar only. Windows assume the supplied work hours; travel, preparation and unrecorded commitments are not included. No time was booked.'}
