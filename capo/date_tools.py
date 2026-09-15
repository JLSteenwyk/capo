"""Local calendar arithmetic shared by every agent and workflow."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .contracts import TEXT, object_schema
from .research_tools import ReadTool


def describe(value, timezone):
    """Derive local date facts from an explicit date or offset-bearing instant."""
    zone = ZoneInfo(timezone)
    if len(value) == 10:
        local = date.fromisoformat(value)
        result = {'kind': 'date', 'value': local.isoformat()}
    else:
        source = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if source.tzinfo is None:
            raise ValueError('Timed values require an explicit UTC offset')
        local = source.astimezone(zone)
        result = {'kind': 'datetime', 'value': local.isoformat(),
                  'utc_offset_seconds': int(local.utcoffset().total_seconds())}
    return {**result, 'date': local.isoformat()[:10], 'timezone': timezone,
            'weekday': ('Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday')[local.weekday()],
            'iso_weekday': local.isoweekday()}


def shift(value, days, timezone):
    """Shift calendar days, preserving local wall time or asking about DST ambiguity."""
    zone = ZoneInfo(timezone)
    if not isinstance(days, str) or not days.lstrip('-').isdigit() or abs(int(days)) > 3660:
        raise ValueError('Use an integer day offset between -3660 and 3660')
    if len(value) == 10:
        result = date.fromisoformat(value) + timedelta(days=int(days))
        return {'value': result.isoformat(), 'timezone': timezone, 'kind': 'date'}
    source = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if source.tzinfo is None:
        raise ValueError('Timed values require an explicit UTC offset')
    local = source.astimezone(zone)
    if local.replace(tzinfo=None) != source.replace(tzinfo=None) or local.utcoffset() != source.utcoffset():
        raise ValueError('The supplied offset and wall time must match the timezone')
    wall = local.replace(tzinfo=None) + timedelta(days=int(days))
    candidates = {}
    for fold in (0, 1):
        candidate = wall.replace(tzinfo=zone, fold=fold)
        if datetime.fromtimestamp(candidate.timestamp(), zone).replace(tzinfo=None) == wall:
            candidates[candidate.isoformat()] = candidate
    if len(candidates) != 1:
        return {'needs_clarification': True, 'local_time': wall.isoformat(), 'timezone': timezone,
                'reason': 'This local time occurs twice.' if candidates else 'This local time does not exist.',
                'choices': list(candidates)}
    return {'value': next(iter(candidates)), 'timezone': timezone, 'kind': 'datetime'}


def tools():
    return [ReadTool('dates.describe',
        'Calculate the weekday, local date and UTC offset for an ISO date or explicit-offset datetime. '
        'Converts instants into the requested timezone; date-only inputs remain calendar dates. '
        'Use verified source dates; this tool does not infer when a source was written.',
        object_schema({'value': TEXT, 'timezone': TEXT}), describe),
        ReadTool('dates.shift',
        'Add or subtract local calendar days from a verified ISO date or offset datetime. '
        'Two weeks before means days=-14. Preserves local time across DST. Does not infer ambiguous dates; '
        'returns a clarification requirement for a nonexistent or repeated destination time.',
        object_schema({'value': TEXT, 'days': TEXT, 'timezone': TEXT}), shift)]
