import tempfile
import unittest
from pathlib import Path
from capo.request_timing import RequestTiming


class TimingTests(unittest.TestCase):
    def test_restart_preserves_first_milestones_and_multiple_activities(self):
        with tempfile.TemporaryDirectory() as tmp:
            clock=RequestTiming(Path(tmp),'synthetic-event')
            clock.mark('message',at=10);clock.mark('received',at=20)
            restarted=RequestTiming(Path(tmp),'synthetic-event')
            restarted.mark('received',at=90)
            for _ in range(2):
                with restarted.activity('tool','calendar.patch'):pass
            rows=restarted.read()
            self.assertEqual(rows[1]['at'],20)
            self.assertEqual(len(rows),6)
            self.assertEqual(restarted.path.stat().st_mode & 0o777,0o600)
            self.assertNotIn('synthetic-event',restarted.path.read_bytes().decode(errors='ignore'))

    def test_exception_closes_activity_and_private_payload_labels_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            clock=RequestTiming(Path(tmp),'event')
            with self.assertRaises(TimeoutError):
                with clock.activity('provider','claude'):raise TimeoutError('private error')
            self.assertEqual([v['phase'] for v in clock.read()],['provider_started','provider_finished'])
            with self.assertRaises(ValueError):clock.mark('completed',label='private error text')
