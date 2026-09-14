"""Local calendar arithmetic shared by every agent and workflow."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .contracts import TEXT, object_schema
from .research_tools import ReadTool


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
    return [ReadTool('dates.shift',
        'Add or subtract local calendar days from a verified ISO date or offset datetime. '
        'Two weeks before means days=-14. Preserves local time across DST. Does not infer ambiguous dates; '
        'returns a clarification requirement for a nonexistent or repeated destination time.',
        object_schema({'value': TEXT, 'days': TEXT, 'timezone': TEXT}), shift)]
