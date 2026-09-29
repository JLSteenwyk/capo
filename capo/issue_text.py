"""Owner-facing wording for Capo's own operational problems.

Producers record structured facts (which request, which check, when, and a
plain cause). This module turns them into one sentence that says what is
affected and what happens next, and decides whether the owner must act.
Internal bookkeeping (receipts, checkpoints, retry mechanics) stays out of
Slack. Deterministic host code: reporting must not depend on an AI provider
whose availability may itself be the problem.
"""
from datetime import datetime
from zoneinfo import ZoneInfo


def _when(seconds, zone):
    try:
        moment = datetime.fromtimestamp(float(seconds), ZoneInfo(zone))
    except (TypeError, ValueError, OverflowError, OSError):
        return ''
    return moment.strftime('%a %b %-d, %-I:%M %p').replace(':00 ', ' ')


def describe(item, zone='America/Los_Angeles'):
    """Return (sentence, owner_must_act) for one operational item."""
    key, status = item.get('id', ''), item.get('status', '')
    if key.startswith('request-health:'):
        # Health output never quotes request text; the thread link identifies it.
        when = _when(item.get('started_at'), zone)
        return ('A request you sent' + (' ' + when if when else '') + ' did not finish. '
                'Reply in its thread to try again' + (': ' + item['url'] if item.get('url') else '.'), True)
    if key.startswith('monitor-failure:'):
        source = item.get('source_label') or 'a new email'
        if status == 'authentication':
            return ('Capo could not review ' + source + ' because a login needs renewing on the computer running Capo.', True)
        return ('Capo could not review ' + source + ' for commitments after ' + str(item.get('attempts', 2)) +
                ' tries, so any related task may be out of date. Please skim it yourself.', True)
    if key.startswith('delivery-unknown:'):
        when = _when(item.get('started_at'), zone)
        return ((item.get('report') or 'A Capo message') + (' from ' + when if when else '') +
                ' may not have been posted to Slack. Capo will not resend it.', False)
    if key.startswith('automation:'):
        title = item.get('title', 'A scheduled check').removeprefix('Scheduled work: ')
        cause = item.get('cause') or 'it reported a problem'
        return (title + ' did not complete: ' + cause + '. It will run again at its next scheduled time.', False)
    service = item.get('service')
    if service:
        if status in ('authentication', 'permission'):
            return ('Capo can no longer read ' + service + '. Reconnect it on the computer running Capo.', True)
        return ('Capo could not reach ' + service + ' this hour; it will try again next hour.', False)
    # Unrecognized producer: keep its own title and explicit next step visible
    # rather than hiding something that may need the owner.
    action = item.get('next_action') or item.get('summary') or ''
    return (item.get('title', 'A Capo background check had a problem') + (': ' + action if action else '.'),
            bool(action) or status == 'authentication')


def compose(items, zone='America/Los_Angeles'):
    """Split operational items into owner actions and one background summary line."""
    actions, background = [], []
    for item in items:
        sentence, act = describe(item, zone)
        (actions if act else background).append(sentence)
    summary = ''
    if background:
        summary = 'Behind the scenes (no action needed): ' + ' '.join(background[:3])
        if len(background) > 3:
            summary += f' Plus {len(background)-3} more; ask “Capo status” for details.'
    return actions, summary
