import tempfile
import unittest
from pathlib import Path
from datetime import datetime,timezone
from unittest.mock import Mock
from capo.notification_policy import NotificationPolicy,filter_hourly


class NotificationTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.home=Path(tmp.name)
        self.policy=NotificationPolicy(self.home,'owner',{'text':'Quiet hours from 22:00 to 08:00 UTC.'})
        self.preferences=dict(quiet_start='22:00',quiet_end='08:00',timezone='UTC',importance='all')
        self.now=datetime(2030,1,1,23,tzinfo=timezone.utc)

    def test_overnight_quiet_window_urgent_bypass_and_reversal(self):
        self.policy.change('',self.preferences,'one')
        self.assertEqual(self.policy.quiet_reason(self.now),'owner_quiet_period')
        self.assertEqual(self.policy.quiet_reason(self.now,important=True),'')
        self.assertEqual(self.policy.quiet_reason(self.now.replace(hour=9)),'')
        self.policy.change('1',dict(self.preferences,quiet_start='',quiet_end=''),'two')
        self.assertEqual(self.policy.quiet_reason(self.now),'')

    def test_owner_scope_receipts_and_stale_revision(self):
        first=self.policy.change('',self.preferences,'one')
        self.assertEqual(self.policy.change('',self.preferences,'one'),first)
        with self.assertRaises(ValueError):self.policy.change('',self.preferences,'new')
        with self.assertRaises(PermissionError):NotificationPolicy(self.home,'owner').change('1',self.preferences,'untrusted')
        self.assertEqual(NotificationPolicy(self.home,'other').quiet_reason(self.now),'')
        self.assertEqual(self.policy.path.stat().st_mode & 0o777,0o600)

    def test_explicit_updates_and_uncertain_delivery_are_never_filtered(self):
        manager=Mock()
        for run in ({'key':'owner:reminder:id','status':'ready'}, {'key':'owner:scheduled:id','status':'ready'},
                    {'key':'owner:heartbeat:id','status':'sending'}, {'key':'owner:heartbeat:id','status':'delivery_unknown'}):
            self.assertTrue(filter_hourly(manager,run,self.now))
        manager.db.save.assert_not_called()

    def test_imminent_event_urgency_requires_selected_evidence_not_prose(self):
        from capo.notification_policy import important_evidence
        item={'id':'event','kind':'calendar','start':{'dateTime':'2030-01-02T00:00:00Z'}}
        self.assertTrue(important_evidence([item],{'event'},self.now))
        self.assertFalse(important_evidence([item],set(),self.now))
        self.assertFalse(important_evidence([{'id':'event','title':'URGENT!'}],{'event'},self.now))

    def test_delivery_filter_records_quiet_checks_but_preserves_important_alerts(self):
        from types import SimpleNamespace
        from capo.capabilities import owner_key
        config={}
        policy=NotificationPolicy(self.home,owner_key(config),{'text':'Use quiet hours.'})
        policy.change('',self.preferences,'delivery-settings')
        manager=SimpleNamespace(service=SimpleNamespace(config=config,store=SimpleNamespace(home=self.home)),db=Mock())
        run={'key':'owner:heartbeat:one','status':'ready','payload':{'text':'Routine update'}}
        self.assertFalse(filter_hourly(manager,run,self.now))
        self.assertEqual(run['status'],'quiet')
        self.assertEqual(run['notification_reason'],'owner_quiet_period')
        manager.db.save.assert_called_once_with(run,self.now)
        urgent={'key':'owner:heartbeat:two','status':'ready','payload':{'host_important':True}}
        self.assertTrue(filter_hourly(manager,urgent,self.now))
        self.assertEqual(urgent['status'],'ready')

    def test_actionable_host_connection_problem_bypasses_important_only_filter(self):
        from capo.notification_policy import important_evidence
        urgent={'id':'auth','kind':'connection','service':'Gmail','status':'authentication'}
        routine={'id':'network','kind':'connection','service':'Gmail','status':'unavailable'}
        self.assertTrue(important_evidence([urgent],{'auth'},self.now))
        self.assertFalse(important_evidence([routine],{'network'},self.now))
        self.assertFalse(important_evidence([urgent],set(),self.now))
