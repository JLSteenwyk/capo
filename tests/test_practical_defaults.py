import tempfile
import unittest
from pathlib import Path
from capo.practical_defaults import PracticalDefaults
from capo.personal_memory import PersonalMemory


class DefaultTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.home=Path(tmp.name)
        self.origin={'event':'synthetic','thread':'thread','text':'I prefer 30-minute events by default. Use 45 minutes instead from now on.'}
        self.defaults=PracticalDefaults(self.home,'owner',self.origin)

    def test_shared_memory_restart_revision_correction_and_deletion(self):
        first=self.defaults.save('event_duration','','I prefer 30-minute events by default.','one')
        self.assertTrue(first['verified'])
        self.assertEqual(PersonalMemory(self.home,'owner').search('defaults/event_duration')['memories'][0]['statement'],'I prefer 30-minute events by default.')
        second=self.defaults.save('event_duration','1','Use 45 minutes instead from now on.','two')
        self.assertEqual(second['memory']['revision'],2)
        restarted=PracticalDefaults(self.home,'owner',self.origin)
        self.assertEqual(len(restarted.read()['defaults']),1)
        self.assertEqual(restarted.read()['defaults'][0]['provenance'],'explicit_owner_quote')
        self.defaults.forget('event_duration','2','remove')
        self.assertEqual(restarted.read()['defaults'],[])

    def test_invented_or_third_party_quote_cannot_be_saved(self):
        with self.assertRaises(ValueError):self.defaults.save('calendar','','Use the work calendar.','invented')
        with self.assertRaises(ValueError):PracticalDefaults(self.home,'owner').save('timezone','','UTC','untrusted')
        with self.assertRaises(ValueError):self.defaults.save('send_permission','','I prefer 30-minute events by default.','grant')

    def test_scope_and_receipts_prevent_owner_leaks_or_stale_edits(self):
        result=self.defaults.save('event_duration','','I prefer 30-minute events by default.','one')
        self.assertEqual(self.defaults.save('event_duration','','I prefer 30-minute events by default.','one'),result)
        self.assertEqual(PracticalDefaults(self.home,'other').read()['defaults'],[])
        with self.assertRaises(ValueError):self.defaults.forget('event_duration','','stale')
        self.assertIn('no permission',self.defaults.read()['coverage'])
        self.assertEqual(self.defaults.memory.path.stat().st_mode & 0o777,0o600)
