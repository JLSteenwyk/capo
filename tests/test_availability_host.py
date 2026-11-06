import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from capo.availability_host import AvailabilityHost,eligible,settings


class AvailabilityTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.home=Path(tmp.name)
        (self.home/'updates').mkdir()
        self.health={'at':100,'connected':True,'cycle_completed':True}
        self.write(self.health)

    def write(self,value):
        (self.home/'updates/health.json').write_text(json.dumps(value))

    def test_only_fresh_connected_working_service_pings_external_monitor(self):
        host=AvailabilityHost(self.home,{'heartbeat_url':'https://monitor.example.invalid/secret'})
        with patch('capo.availability_host.ping') as ping:
            self.assertEqual(host.step(101),'accepted');ping.assert_called_once()
            ping.reset_mock()
            for value in ({},dict(self.health,connected=False),dict(self.health,cycle_completed=False),dict(self.health,at=0),dict(self.health,at=200)):
                self.write(value);self.assertEqual(host.step(101),'withheld')
            ping.assert_not_called()
        self.assertNotIn('secret',(self.home/'availability.json').read_text())
        self.assertEqual((self.home/'availability.json').stat().st_mode & 0o777,0o600)

    def test_sleep_resume_does_not_publish_stale_health_or_replay_any_work(self):
        host=AvailabilityHost(self.home,{'heartbeat_url':'https://monitor.example.invalid/ping'})
        with patch('capo.availability_host.ping') as ping:
            self.assertEqual(host.step(200),'withheld')
            self.write(dict(self.health,at=200))
            self.assertEqual(host.step(201),'accepted');ping.assert_called_once()

    def test_ac_only_assertion_no_display_or_battery_assertion_and_cleanup(self):
        host=AvailabilityHost(self.home,{'prevent_ac_sleep':True})
        with patch('capo.availability_host.sys.platform','darwin'),patch('capo.availability_host.subprocess.Popen') as popen:
            popen.return_value.poll.return_value=None
            host.step(101);host.step(102)
            argv=popen.call_args.args[0]
            self.assertEqual(argv[:3],['/usr/bin/caffeinate','-s','-w'])
            popen.assert_called_once();host.close();popen.return_value.terminate.assert_called_once()

    def test_missing_monitor_is_explicit_and_ping_failure_is_not_healthy_claim(self):
        self.assertEqual(AvailabilityHost(self.home,{}).step(101),'unconfigured')
        host=AvailabilityHost(self.home,{'heartbeat_url':'https://monitor.example.invalid/ping'})
        with patch('capo.availability_host.ping',side_effect=TimeoutError('secret')):
            self.assertEqual(host.step(101),'failed')
        self.assertNotIn('secret',(self.home/'availability.json').read_text())

    def test_configuration_rejects_invalid_bounds_and_private_url(self):
        for value in ({'interval':0},{'max_health_age':True},{'prevent_ac_sleep':1},{'heartbeat_url':'http://localhost/key'}):
            with self.assertRaises(ValueError):settings(value)
