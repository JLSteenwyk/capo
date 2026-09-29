import re
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock
from zoneinfo import ZoneInfo

from capo.delivery_recovery import defer
from capo.heartbeat import select
from capo.health import cause
from capo.issue_text import describe

NOW = datetime(2030, 1, 3, 13, tzinfo=ZoneInfo('America/Los_Angeles'))
JARGON = re.compile(r'receipt|checkpoint|preserved|verification|tool result|reconcil|inspect', re.I)

OWNER_TASK = {'id': 'task-1', 'kind': 'personal_task', 'title': 'Submit interview availability',
              'task_id': 't1', 'notice_day': '2030-01-03'}
BACKGROUND = [
    {'id': 'automation:watch:needs_attention', 'kind': 'connection', 'title': 'Coding Agent: GitHub profile watch',
     'status': 'needs_attention', 'cause': 'it stopped partway through',
     'next_action': '2 tool results preserved. The scan is incomplete; no verified monitoring report was produced.'},
    {'id': 'delivery-unknown:abc', 'kind': 'connection', 'title': 'Report delivery could not be confirmed',
     'status': 'delivery_unknown', 'report': 'An hourly check', 'started_at': NOW.timestamp() - 3 * 86400,
     'next_action': 'Slack delivery is unconfirmed. The saved message will only be checked in history, never automatically resent.'},
]
OWNER_ACTION = {'id': 'monitor-failure:k', 'kind': 'connection', 'title': 'Commitment review needs attention',
                'status': 'review_failed', 'source_label': '“Fw: meeting files”', 'attempts': 2,
                'summary': 'The work stopped before completion. Saved progress and action receipts are preserved.'}


class HourlyCheckTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def test_capo_problems_never_crowd_out_owner_items_and_are_plainly_worded(self):
        provider = Mock()
        provider.call.return_value = {'alerts': [{'id': 'task-1', 'reason': 'Due today; send your availability.'}]}
        result = select([OWNER_TASK, OWNER_ACTION, *BACKGROUND], {}, NOW, self.dir, provider)
        text = result['text']
        # The model sees only owner items; host code words Capo's own problems.
        sent = provider.call.call_args.args[1]
        self.assertIn('task-1', sent); self.assertNotIn('monitor-failure', sent)
        attention, _, background = text.partition('\n\n')
        self.assertIn('• Submit interview availability: Due today; send your availability.', attention)
        self.assertIn('• Capo could not review “Fw: meeting files” for commitments after 2 tries', attention)
        self.assertTrue(background.startswith('Behind the scenes (no action needed): '))
        self.assertIn('Coding Agent: GitHub profile watch did not complete: it stopped partway through.', background)
        self.assertIn('An hourly check from Mon Dec 31, 1 PM may not have been posted to Slack.', background)
        self.assertIsNone(JARGON.search(text), text)
        self.assertEqual({row['id'] for row in result['news']},
                         {'task-1', OWNER_ACTION['id'], *(item['id'] for item in BACKGROUND)})

    def test_capo_problems_are_still_reported_when_the_model_is_down(self):
        provider = Mock(); provider.call.side_effect = RuntimeError('provider down')
        text = select([OWNER_TASK, OWNER_ACTION, *BACKGROUND], {}, NOW, self.dir, provider)['text']
        self.assertIn('Capo could not review', text); self.assertIn('Behind the scenes', text)
        with self.assertRaises(RuntimeError):
            select([OWNER_TASK], {}, NOW, self.dir, provider)

    def test_each_known_problem_names_what_is_affected_and_whether_to_act(self):
        cases = [
            ({'id': 'request-health:x', 'started_at': NOW.timestamp(), 'url': 'https://app.slack.com/archives/C/p1'},
             'A request you sent Thu Jan 3, 1 PM did not finish. Reply in its thread to try again: https://app.slack.com/archives/C/p1', True),
            ({'id': 'health', 'service': 'gmail', 'status': 'authentication'},
             'Capo can no longer read gmail. Reconnect it on the computer running Capo.', True),
            ({'id': 'health', 'service': 'calendar', 'status': 'temporary'},
             'Capo could not reach calendar this hour; it will try again next hour.', False),
            ({'id': 'automation:a:missed', 'title': 'Scheduled work: Money Saver', 'cause': cause(None, 'missed')},
             'Money Saver did not complete: it did not run at its scheduled time. It will run again at its next scheduled time.', False),
        ]
        for item, sentence, act in cases:
            self.assertEqual(describe(item, 'America/Los_Angeles'), (sentence, act))

    def test_causes_come_from_recorded_state(self):
        self.assertEqual(cause({'status': 'sent', 'error_summary': 'The requested action is outside the current permission or task scope.'}, 'needs_attention'),
                         'it stopped when a source it tried to read was off-limits')
        self.assertEqual(cause({'status': 'expired'}, 'needs_attention'), 'it did not finish in time')
        self.assertEqual(cause({'status': 'sent', 'outcome_status': 'partial'}, 'needs_attention'), 'it only covered part of what it checks')


class UndeliveredTests(unittest.TestCase):
    def test_only_repeated_complete_checks_over_a_day_conclude_not_posted(self):
        run = {'status': 'delivery_unknown', 'sending_at': 0.0}
        for hour in (1, 2, 3):
            defer(run, hour * 3600.0, history_complete=True)
        self.assertEqual(run['status'], 'delivery_unknown')   # Under a day old.
        incomplete = dict(run); defer(incomplete, 90000.0, history_complete=False)
        self.assertEqual(incomplete['status'], 'delivery_unknown')
        defer(run, 90000.0, history_complete=True)
        self.assertEqual((run['status'], run['delivery_error']), ('undelivered', 'not_found'))


class MissingCommandTests(unittest.TestCase):
    def test_absent_command_is_reported_as_temporary_unavailability(self):
        from capo.process import run_process
        from capo.recovery import failure
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError) as caught:
                run_process(['capo-test-command-that-does-not-exist'], tmp, Path(tmp)/'attempt', 10)
            self.assertEqual(failure(caught.exception, 5.0), ('unavailable', 5.0))


if __name__ == '__main__':
    unittest.main()
