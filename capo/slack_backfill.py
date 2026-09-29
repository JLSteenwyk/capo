"""Recover owner messages Slack never delivered while Capo was disconnected.

Socket Mode does not replay events sent during an outage. After each
(re)connection and periodically, read the configured channel and its recently active threads,
and queue owner messages that Capo never received through the normal ingest
path. Authorization, deduplication and processing are unchanged; recovered
messages keep their original timestamp so relative dates resolve from when
they were sent.
"""
import json
import time

from .message_format import slack_timestamp

LOOKBACK_SECONDS = 24 * 3600
RECHECK_SECONDS = 300  # Also recheck periodically; one read can be transiently incomplete.
MAX_MESSAGES = 20
MAX_PAGES = 5


def known_timestamps(store, channel):
    """Owner message timestamps Capo already received, live or recovered."""
    seen = set()
    for (data,) in store.db.execute('SELECT data FROM slack_inbox'):
        event = json.loads(data).get('event', {})
        if event.get('channel') == channel and event.get('ts'):
            seen.add(event['ts'])
    return seen


def _pages(call, **arguments):
    cursor = ''
    for _ in range(MAX_PAGES):
        result = call(**arguments, cursor=cursor, limit=200)
        yield result.get('messages', [])
        cursor = result.get('response_metadata', {}).get('next_cursor', '')
        if not cursor:
            return


def missed(client, config, bot_user_id, seen, now=None):
    """Owner messages in the lookback window with no receipt and no later Capo reply."""
    now = time.time() if now is None else now
    channel, owner, oldest = config['channel_id'], config['owner_user_id'], now - LOOKBACK_SECONDS
    candidates = []
    for page in _pages(client.conversations_history, channel=channel, oldest=slack_timestamp(oldest - 7 * 86400)):
        for message in page:
            recent_thread = message.get('reply_count') and float(message.get('latest_reply', 0)) >= oldest
            thread = []
            if recent_thread:
                for replies in _pages(client.conversations_replies, channel=channel, ts=message['ts']):
                    thread.extend(replies)
            if float(message['ts']) >= oldest:
                candidates.append((message, thread[1:] if thread else []))
            for index, reply in enumerate(thread[1:], 1):
                if float(reply['ts']) >= oldest:
                    candidates.append((reply, thread[index + 1:]))
    found = []
    for message, later in candidates:
        if (message.get('user') != owner or message.get('bot_id') or message['ts'] in seen
                or message.get('subtype') not in (None, 'file_share')):
            continue
        # A later Capo reply in the same thread means it was already answered.
        if any(reply.get('user') == bot_user_id for reply in later):
            continue
        found.append(message)
    found.sort(key=lambda m: float(m['ts']))
    return found[-MAX_MESSAGES:]


def backfill(store, config, client, bot_user_id, now=None):
    """Queue missed owner messages through ingest; returns how many were queued."""
    from .slack import ingest
    seen = known_timestamps(store, config['channel_id'])
    queued = 0
    for message in missed(client, config, bot_user_id, seen, now):
        event = {key: message[key] for key in ('ts', 'text', 'user', 'thread_ts', 'files', 'subtype') if key in message}
        event.update(type='message', channel=config['channel_id'], capo_recovered=True)
        body = {'team_id': config['team_id'], 'event_id': 'recovered:' + config['channel_id'] + ':' + message['ts'],
                'event': event}
        queued += bool(ingest(store.home, config, body))
    return queued
