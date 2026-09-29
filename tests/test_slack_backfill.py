import tempfile
import unittest
from pathlib import Path

from capo.slack import authorized, ingest, owner_message_record
from capo.slack_backfill import backfill
from capo.store import Store

CONFIG = {'team_id': 'T1', 'channel_id': 'C1', 'owner_user_id': 'UOWNER'}
NOW = 1_900_000_000.0
HOUR = 3600


def msg(ts, text, user='UOWNER', **extra):
    return dict(ts=f'{ts:.6f}', text=text, user=user, **extra)


class FakeSlack:
    def __init__(self, history, threads):
        self.history, self.threads, self.calls = history, threads, []

    def conversations_history(self, channel, oldest, cursor, limit):
        self.calls.append('history')
        return {'messages': [m for m in self.history if float(m['ts']) >= float(oldest)]}

    def conversations_replies(self, channel, ts, cursor, limit):
        self.calls.append('replies')
        return {'messages': self.threads[ts]}


class BackfillTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.store = Store(Path(tmp.name)); self.addCleanup(self.store.db.close)
        missed = msg(NOW-2*HOUR, 'Create a reminder for tomorrow at 10')
        answered = msg(NOW-3*HOUR, 'Answered already', reply_count=1, latest_reply=f'{NOW-3*HOUR+60:.6f}')
        received = msg(NOW-4*HOUR, 'Received live, still processing')
        old_thread = msg(NOW-48*HOUR, 'Old request', reply_count=2, latest_reply=f'{NOW-HOUR:.6f}')
        self.thread_reply = msg(NOW-HOUR, 'Follow-up in an old thread', thread_ts=old_thread['ts'])
        self.history = [missed, answered, received, old_thread,
                        msg(NOW-5*HOUR, 'Bot notice', user='UBOT', bot_id='B1'),
                        msg(NOW-5*HOUR+1, 'Someone else', user='UOTHER'),
                        msg(NOW-30*HOUR, 'Too old to recover'),
                        msg(NOW-6*HOUR, 'joined', subtype='channel_join')]
        self.threads = {answered['ts']: [answered, msg(NOW-3*HOUR+60, 'Done.', user='UBOT', bot_id='B1')],
                        old_thread['ts']: [old_thread, msg(NOW-47*HOUR, 'Earlier answer', user='UBOT', bot_id='B1'), self.thread_reply]}
        self.missed, self.received = missed, received
        live = {'team_id': 'T1', 'event_id': 'Ev1', 'event': dict(received, type='message', channel='C1')}
        self.assertTrue(ingest(self.store.home, CONFIG, live))

    def queued(self):
        return [(event_id, body['event']) for event_id, body in self.store.pending_slack()]

    def test_queues_only_unanswered_unreceived_owner_messages_including_thread_replies(self):
        client = FakeSlack(self.history, self.threads)
        self.assertEqual(backfill(self.store, CONFIG, client, 'UBOT', now=NOW), 2)
        recovered = {event['ts']: (event_id, event) for event_id, event in self.queued() if event.get('capo_recovered')}
        self.assertEqual(set(recovered), {self.missed['ts'], self.thread_reply['ts']})
        event_id, event = recovered[self.thread_reply['ts']]
        self.assertEqual((event_id, event['thread_ts'], event['channel']),
                         ('recovered:C1:'+self.thread_reply['ts'], self.thread_reply['thread_ts'], 'C1'))
        # Recovered messages pass the same authorization as live ones.
        self.assertTrue(all(authorized(CONFIG, body) for _, body in self.store.pending_slack()))
        self.assertEqual(backfill(self.store, CONFIG, client, 'UBOT', now=NOW), 0)

    def test_late_live_delivery_and_recovery_are_one_message_in_either_order(self):
        backfill(self.store, CONFIG, FakeSlack(self.history, self.threads), 'UBOT', now=NOW)
        late = {'team_id': 'T1', 'event_id': 'Ev2', 'event': dict(self.missed, type='message', channel='C1')}
        self.assertFalse(ingest(self.store.home, CONFIG, late))
        self.assertEqual(sum(event['ts'] == self.missed['ts'] for _, event in self.queued()), 1)
        # Live first: the received message is not recovered again.
        self.assertEqual(sum(event['ts'] == self.received['ts'] for _, event in self.queued()), 1)

    def test_recovered_record_keeps_send_time_and_explains_late_delivery(self):
        record = owner_message_record('Create a reminder for tomorrow', dict(self.missed, capo_recovered=True))
        self.assertTrue(record['sent_at'].startswith('2030-03-17T'))
        self.assertIn('from sent_at', record['delivery_note'])
        self.assertNotIn('delivery_note', owner_message_record('Live', self.missed))


if __name__ == '__main__':
    unittest.main()
