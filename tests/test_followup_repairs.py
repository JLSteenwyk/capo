import json
import tempfile
import unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import Mock,patch
from capo.check_recovery import queue_followups
from capo.digest import DigestStore
from capo.monitoring import Monitor
from capo.message_format import plain_text,slack_text


class FollowupRepairs(unittest.TestCase):
    def test_followup_is_once_only_current_enabled_revision_and_keeps_original(self):
        now=datetime(2030,1,3,20,tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            db=DigestStore(Path(tmp));self.addCleanup(db.close)
            schedule={'id':'s','revision':1,'enabled':True,'timezone':'America/Los_Angeles','weekdays':list('01234')}
            parent={'key':'original','scope':'owner','day':'2030-01-03','status':'sent','created':now.timestamp()-3600,
                'deadline':now.timestamp()-1800,'schedule_id':'s','revision':1,'request':'Inspect sources',
                'title':'Review','identity':{},'outcome_status':'partial'}
            db.save(parent,now-timedelta(minutes=30))
            queue_followups(db,'other',{'s':schedule},now)
            self.assertIsNone(db.get('original:followup'))
            for change in ({'enabled':False},{'revision':2},{'weekdays':['6']}):
                queue_followups(db,'owner',{'s':dict(schedule,**change)},now)
                self.assertIsNone(db.get('original:followup'))
            queue_followups(db,'owner',{'s':schedule},now)
            child=db.get('original:followup')
            self.assertEqual(child['recovery_of'],'original');self.assertEqual(child['deadline'],now.timestamp()+900)
            self.assertEqual(db.get('original'),parent)
            child.update(status='sent',outcome_status='partial');db.save(child,now)
            queue_followups(db,'owner',{'s':schedule},now+timedelta(hours=1))
            self.assertEqual(db.db.execute('SELECT COUNT(*) FROM runs').fetchone()[0],2)

    def test_old_and_successful_checks_are_not_replayed(self):
        now=datetime(2030,1,3,20,tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            db=DigestStore(Path(tmp));self.addCleanup(db.close)
            s={'revision':1,'enabled':True,'timezone':'UTC','weekdays':list('0123456')}
            for status,age in [('sent',3600),('failed',86400),('failed',60)]:
                r={'key':str(age)+status,'scope':'owner','day':'2030-01-03','status':status,
                   'created':now.timestamp()-age,'schedule_id':'s','revision':1}
                db.save(r,now);queue_followups(db,'owner',{'s':s},now)
                self.assertIsNone(db.get(r['key']+':followup'))

    def test_failed_monitor_does_not_starve_other_sources_and_retry_is_bounded(self):
        now=datetime(2030,1,3,20,tzinfo=timezone.utc)
        first={'id':'a','kind':'email','message_id':'a','title':'First obligation'}
        second={'id':'b','kind':'email','message_id':'b','title':'Another obligation'}
        with tempfile.TemporaryDirectory() as tmp:
            m=Monitor(Path(tmp),{})
            with patch('capo.monitoring.shared_tools') as shared,patch('capo.monitoring.research') as run:
                shared.return_value.tools={}
                run.side_effect=[{'status':'partial'},{'status':'reported_complete'},{'status':'partial'}]
                m.tick(now,[first]);m=Monitor(Path(tmp),{});m.tick(now+timedelta(minutes=1),[first,second])
                self.assertEqual(run.call_count,2)
                self.assertEqual(run.call_args.args[2]['observations'][0]['message_id'],'b')
                m.tick(now+timedelta(minutes=2),[first,second]);self.assertEqual(run.call_count,2)
                m.tick(now+timedelta(hours=2),[first,second]);self.assertEqual(run.call_count,3)
                self.assertEqual(run.call_args.args[3].name,'followup')
                m.tick(now+timedelta(hours=4),[first,second]);self.assertEqual(run.call_count,3)
                self.assertTrue(m.notices())

    def test_paragraph_after_link_and_punctuation_do_not_enter_url(self):
        raw=r'See https://example.org/pull/123\n\nThe change is merged.'
        rendered=slack_text(plain_text(raw))
        self.assertIn('<https://example.org/pull/123|example.org>\n\nThe',rendered)
        self.assertEqual(slack_text('Facts (https://example.org/post):'),
                         'Facts (<https://example.org/post|example.org>):')
        self.assertEqual(plain_text(r'`https://example.org/a\n\nThe`'),r'`https://example.org/a\n\nThe`')
