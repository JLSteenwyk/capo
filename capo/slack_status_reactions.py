"""Emoji status on the owner's own Slack messages.

👀 marks a request Capo has picked up; it is replaced by ✅ when the reply
is delivered and the request completed, or ⚠️ when Capo could not finish.
When the conversation decides a message needs no text answer (a thank-you,
an "ok"), it records an acknowledgment through `slack.acknowledge` and Capo
reacts 👍 instead of posting a reply.

Reactions are optional presentation: every Slack failure is swallowed so it
can never block or repeat the actual reply. Only the owner's authorized
message in the configured channel is ever reacted to; the event comes from
the host inbox, never from model arguments. State is durable, so a pending
request does not trigger an API call on every service tick.
"""
import hashlib
import json

from .contracts import object_schema
from .research_tools import ReadTool

RECEIVED, DONE, FAILED, ACKNOWLEDGED = 'eyes', 'white_check_mark', 'warning', '+1'
_TABLE = ('CREATE TABLE IF NOT EXISTS slack_status_reactions('
          'event_id TEXT PRIMARY KEY, state TEXT NOT NULL, acknowledged INTEGER NOT NULL DEFAULT 0)')


def _table(db):
    db.execute(_TABLE)


def acknowledged(store, event_id):
    _table(store.db)
    row = store.db.execute('SELECT acknowledged FROM slack_status_reactions WHERE event_id=?', (event_id,)).fetchone()
    return bool(row and row[0])


def outcome_status(home, config, event_id):
    """Recorded status of a capability conversation; empty when none was recorded."""
    from .capabilities import owner_key
    directory = (home / 'capabilities' / hashlib.sha256(owner_key(config).encode()).hexdigest()
                 / 'conversation' / hashlib.sha256(str(event_id).encode()).hexdigest())
    try:
        return json.loads((directory / 'outcome.json').read_text()).get('status', '')
    except (OSError, ValueError, AttributeError):
        return ''


class StatusReactions:
    def __init__(self, store, config, client):
        self.store, self.config, self.client = store, config, client

    def _state(self, event_id):
        _table(self.store.db)
        row = self.store.db.execute('SELECT state FROM slack_status_reactions WHERE event_id=?', (event_id,)).fetchone()
        return row[0] if row else ''

    def _save(self, event_id, state):
        with self.store.db:
            _table(self.store.db)
            self.store.db.execute('INSERT INTO slack_status_reactions(event_id,state) VALUES (?,?) '
                                  'ON CONFLICT(event_id) DO UPDATE SET state=excluded.state', (event_id, state))

    def _call(self, method, event, name):
        call = getattr(self.client, method, None)
        if call is None:
            return
        try:
            call(channel=self.config['channel_id'], timestamp=event['ts'], name=name)
        except Exception:
            pass  # already_reacted, no_reaction, missing_scope, network: presentation only.

    def received(self, event_id, event):
        if not self._state(event_id):
            self._call('reactions_add', event, RECEIVED)
            self._save(event_id, 'received')

    def finished(self, event_id, event, failed=False):
        """Swap 👀 for the final status once the reply is delivered."""
        if self._state(event_id) in ('done', 'failed', 'acknowledged'):
            return
        if acknowledged(self.store, event_id):
            final, state = ACKNOWLEDGED, 'acknowledged'
        elif failed or outcome_status(self.store.home, self.config, event_id) in ('partial', 'failed'):
            final, state = FAILED, 'failed'
        else:
            final, state = DONE, 'done'
        self._call('reactions_remove', event, RECEIVED)
        self._call('reactions_add', event, final)
        self._save(event_id, state)


class AcknowledgeTool:
    """Let the conversation answer a message that needs no text with 👍."""

    def __init__(self, home, config, request):
        self.home, self.config, self.request = home, config, request

    def acknowledge(self, operation_id=None):
        from .store import Store
        from .slack import authorized
        event_id = (self.request.get('owner_request') or {}).get('event')
        store = Store(self.home)
        try:
            row = store.db.execute('SELECT data FROM slack_inbox WHERE id=?', (event_id,)).fetchone()
            if row is None or not authorized(self.config, json.loads(row[0]), store):
                raise PermissionError('Only an authenticated owner message can be acknowledged')
            with store.db:
                _table(store.db)
                store.db.execute('INSERT INTO slack_status_reactions(event_id,state,acknowledged) VALUES (?,?,1) '
                                 'ON CONFLICT(event_id) DO UPDATE SET acknowledged=1', (event_id, 'received'))
        finally:
            store.db.close()
        return {'saved': True, 'acknowledged': True,
                'coverage': 'Capo will react 👍 to this message and post no text reply.'}

    def tool(self):
        return ReadTool('slack.acknowledge',
            'React 👍 to the owner’s current Slack message instead of replying, when it needs no substantive answer '
            '(thanks, ok, got it, sounds good). Your finish reply is then not posted, so do not use this when the owner '
            'asked a question or requested anything. Only the current authenticated owner message can be acknowledged.',
            object_schema({}), self.acknowledge, mutates=True)
