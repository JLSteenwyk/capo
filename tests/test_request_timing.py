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

    def test_summary_separates_transport_delay_queue_and_execution(self):
        from capo.request_timing import summary
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp);clock=RequestTiming(home,'private-id')
            for phase,at in [('message',10),('received',100),('acknowledged',102),('execution_started',110),('first_reply',115),('completed',120),('delivered',122)]:clock.mark(phase,at=at)
            result=summary(home,now=130)
            self.assertEqual(result['metrics']['delivery_to_host']['median_seconds'],90)
            self.assertEqual(result['metrics']['queue']['median_seconds'],10)
            self.assertEqual(result['metrics']['execution']['median_seconds'],10)
            self.assertNotIn('private-id',str(result))
