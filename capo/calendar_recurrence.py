"""Validated common RFC 5545 recurrence rules for personal event creation."""
import re
from datetime import datetime,timezone


def rule(value, all_day=None, start=None):
    if isinstance(value,str) and value.startswith('FREQ='):value='RRULE:'+value
    if not isinstance(value,str) or len(value)>300 or not value.startswith('RRULE:'):
        raise ValueError('Use one RRULE with daily, weekly, monthly or yearly frequency.')
    parts={}
    for token in value[6:].split(';'):
        key,sep,item=token.partition('=')
        if not sep or key in parts or not item:raise ValueError('Invalid or repeated recurrence field')
        parts[key]=item
    if set(parts)-{'FREQ','INTERVAL','COUNT','UNTIL','BYDAY','BYMONTHDAY','BYMONTH','WKST'}:
        raise ValueError('Unsupported recurrence field')
    if parts.get('FREQ') not in ('DAILY','WEEKLY','MONTHLY','YEARLY'):raise ValueError('Unsupported recurrence frequency')
    if 'COUNT' in parts and 'UNTIL' in parts:raise ValueError('Choose a count or end date, not both')
    for key,limit in (('INTERVAL',366),('COUNT',10000)):
        if key in parts:
            if not parts[key].isdigit() or not 1<=int(parts[key])<=limit:raise ValueError('Invalid recurrence '+key)
            parts[key]=str(int(parts[key]))
    if parts.get('INTERVAL')=='1':parts.pop('INTERVAL')
    for key,low,high in (('BYMONTHDAY',-31,31),('BYMONTH',1,12)):
        if key in parts:
            values=parts[key].split(',')
            if len(values)>62 or any(not re.fullmatch(r'-?\d{1,2}',v) or not low<=int(v)<=high or int(v)==0 for v in values):raise ValueError('Invalid recurrence '+key)
            parts[key]=','.join(str(v) for v in sorted(set(map(int,values))))
    if 'BYMONTHDAY' in parts and parts['FREQ']=='WEEKLY':raise ValueError('Weekly rules use weekdays, not month days')
    if 'BYDAY' in parts:
        values=parts['BYDAY'].split(',')
        if len(values)>62 or any(not re.fullmatch(r'(?:-?[1-5])?(?:MO|TU|WE|TH|FR|SA|SU)',v) for v in values):raise ValueError('Invalid recurrence weekdays')
        if parts['FREQ'] in ('DAILY','WEEKLY') and any(len(v)>2 for v in values):raise ValueError('Numbered weekdays require monthly or yearly frequency')
        parts['BYDAY']=','.join(sorted(set(values)))
    if start and parts['FREQ'] in ('DAILY','WEEKLY') and 'BYDAY' in parts:
        weekday=('MO','TU','WE','TH','FR','SA','SU')[datetime.fromisoformat(start.replace('Z','+00:00')).weekday()]
        if weekday not in parts['BYDAY'].split(','):raise ValueError('The first event must fall on a requested weekday')
    if 'WKST' in parts and parts['WKST'] not in ('MO','TU','WE','TH','FR','SA','SU'):raise ValueError('Invalid week start')
    if parts.get('WKST')=='MO':parts.pop('WKST')
    if 'UNTIL' in parts:
        value=parts['UNTIL']
        if not re.fullmatch(r'\d{8}(?:T\d{6}Z)?',value):raise ValueError('Use a date or UTC timestamp for UNTIL')
        timed='T' in value
        until=datetime.strptime(value,'%Y%m%dT%H%M%SZ' if timed else '%Y%m%d').replace(tzinfo=timezone.utc)
        if all_day is not None and timed==all_day:raise ValueError('All-day UNTIL requires a date; timed UNTIL requires UTC')
        if start:
            beginning=datetime.fromisoformat(start.replace('Z','+00:00'))
            if beginning.tzinfo is None:beginning=beginning.replace(tzinfo=timezone.utc)
            if until<beginning:raise ValueError('Recurrence ends before its first event')
    return 'RRULE:'+';'.join(k+'='+parts[k] for k in sorted(parts))


def equivalent_rules(left,right,start=None):
    def canonical(value):
        normalized=rule(value)
        parts=dict(item.split('=') for item in normalized[6:].split(';'))
        if start and parts['FREQ']=='WEEKLY' and 'BYDAY' not in parts:
            parts['BYDAY']=('MO','TU','WE','TH','FR','SA','SU')[datetime.fromisoformat(start.replace('Z','+00:00')).weekday()]
        return sorted(parts.items())
    try:return sorted(canonical(v) for v in left)==sorted(canonical(v) for v in right)
    except (ValueError,TypeError):return False
