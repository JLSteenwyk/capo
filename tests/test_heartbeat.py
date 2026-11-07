import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from zoneinfo import ZoneInfo
from capo.heartbeat import settings,due_slot,select,HeartbeatManager
from capo.store import Store

class HeartbeatTests(unittest.TestCase):
    def test_hours_inclusive_local_time_and_no_overnight_catchup(self):
        p=settings({'heartbeat':{'enabled':True}})
        for month in (1,7):
            for hour in range(24):
                now=datetime(2030,month,3,hour,0,tzinfo=ZoneInfo('America/Los_Angeles'))
                self.assertEqual(due_slot(now,p) is not None,10<=hour<=16)
            late=datetime(2030,month,3,10,10,tzinfo=ZoneInfo('America/Los_Angeles'))
            self.assertIsNone(due_slot(late,p))
        self.assertIsNone(due_slot(now,settings({})))

    def test_selection_deduplicates_and_rejects_invented_ids(self):
        now=datetime(2030,1,3,10,tzinfo=ZoneInfo('America/Los_Angeles'))
        provider=Mock();provider.call.return_value={'alerts':[{'id':'invented','reason':'Bad'}, {'id':'a','reason':'Please review.'}]}
        items=[{'id':'a','title':'A request'}]
        result=select(items,{},now,Path('/synthetic'),provider)
        self.assertEqual(result['news'],[{'id':'a','day':'2030-01-03'}])
        self.assertNotIn('Bad',result['text'])
        provider.reset_mock()
        result=select(items,{'a':{'day':'2030-01-03'}},now,Path('/synthetic'),provider)
        self.assertEqual(result['text'],'');provider.call.assert_not_called()

    def test_quiet_receipt_survives_restart_without_duplicate_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Path(tmp));self.addCleanup(store.db.close)
            config={'team_id':'T123','channel_id':'C123','owner_user_id':'U123','heartbeat':{'enabled':True}}
            client=Mock()
            service=SimpleNamespace(store=store,config=config,client=client)
            manager=HeartbeatManager(service)
            now=datetime(2030,1,3,10,tzinfo=ZoneInfo('America/Los_Angeles'))
            with patch('capo.heartbeat.evidence',return_value=[]) as read:
                manager.tick(now)
                for worker in manager.workers.values():worker.join(3)
                manager.tick(now)
                manager.db.close()
                restarted=HeartbeatManager(service)
                try:restarted.tick(now)
                finally:restarted.db.close()
                read.assert_called_once()
                client.chat_postMessage.assert_not_called()

    def test_alert_delivered_once_and_stale_work_not_sent(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Path(tmp));self.addCleanup(store.db.close)
            config={'team_id':'T123','channel_id':'C123','owner_user_id':'U123','heartbeat':{'enabled':True}}
            client=Mock();client.chat_postMessage.return_value={'ok':True,'ts':'123.4'}
            service=SimpleNamespace(store=store,config=config,client=client,bot_user_id='UBOT')
            manager=HeartbeatManager(service);self.addCleanup(manager.db.close)
            now=datetime(2030,1,3,10,tzinfo=ZoneInfo('America/Los_Angeles'))
            with patch('capo.heartbeat.evidence',return_value=[]), patch('capo.heartbeat.select',return_value={'text':'Review a request.','news':[{'id':'a','day':'2030-01-03'}]}):
                manager.tick(now)
                for worker in manager.workers.values():worker.join(3)
                manager.tick(now);manager.tick(now)
                client.chat_postMessage.assert_called_once()
                self.assertIn('a',manager.db.history(manager.owner))
                manager.tick(now.replace(hour=11))
                for worker in manager.workers.values():worker.join(3)
                manager.tick(now.replace(hour=17))
                client.chat_postMessage.assert_called_once()

    def test_latest_scheduled_failure_is_reported_without_reviving_old_failures(self):
        from capo.digest import DigestStore, scope
        from capo.heartbeat import evidence
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config = {'team_id':'T123','channel_id':'C123','owner_user_id':'U123'}
            now = datetime(2030,1,3,10,tzinfo=ZoneInfo('America/Los_Angeles'))
            db = DigestStore(home/'scheduled')
            try:
                run = {'key':'failed-week','scope':scope(config),'status':'failed','schedule_id':'weekly',
                       'title':'Weekly plan','day':'2030-01-03','error_summary':'The AI login needs renewal.'}
                db.save(run, now)
                items = evidence(config, [], now, home)
                self.assertEqual(len(items), 1)
                self.assertIn('login', items[0]['summary'])
                db.save(dict(run, key='successful-week', status='sent'), now)
                self.assertEqual(evidence(config, [], now, home), [])
            finally:
                db.close()

    def test_selected_source_links_are_host_rendered_and_invalid_links_omitted(self):
        now=datetime(2030,1,3,10,tzinfo=ZoneInfo('America/Los_Angeles'))
        provider=Mock();provider.call.return_value={'alerts':[{'id':'event','reason':'Prepare for the meeting.'},{'id':'private','reason':'Review.'}]}
        items=[{'id':'event','title':'Meeting','url':'https://calendar.google.com/calendar/event?eid=synthetic'},
               {'id':'private','title':'Private task','url':'https://user:secret@example.invalid/path'}]
        result=select(items,{},now,Path('/synthetic'),provider)
        from capo.message_format import slack_text
        text=slack_text(result['text'])
        self.assertIn('<https://calendar.google.com/calendar/event?eid=synthetic|calendar.google.com>',text)
        self.assertNotIn('secret',text)
        self.assertNotIn('user:',text)

    def test_daily_notification_setting_preserves_hourly_check_slots(self):
        config={'heartbeat':{'enabled':True,'start_hour':10,'end_hour':16,'notify_hour':15}}
        policy=settings(config)
        self.assertEqual(policy['notify_hour'],15)
        for hour in range(10,17):
            now=datetime(2030,1,7,hour,tzinfo=ZoneInfo('America/Los_Angeles'))
            self.assertIsNotNone(due_slot(now,policy))
        for bad in (9,17,'15',True):
            with self.assertRaises(ValueError):settings({'heartbeat':dict(config['heartbeat'],notify_hour=bad)})

    def test_quiet_morning_check_runs_and_daily_slot_posts_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Path(tmp));self.addCleanup(store.db.close)
            config={'team_id':'T1','channel_id':'C1','owner_user_id':'U1',
                    'heartbeat':{'enabled':True,'start_hour':10,'end_hour':16,'notify_hour':15}}
            client=Mock();client.chat_postMessage.return_value={'ok':True,'ts':'200.001'}
            service=SimpleNamespace(store=store,config=config,client=client,bot_user_id='BOT')
            manager=HeartbeatManager(service);self.addCleanup(manager.db.close)
            now=datetime(2030,1,7,10,tzinfo=ZoneInfo('America/Los_Angeles'))
            payload={'text':'Needs your attention: review the saved request.','news':[]}
            with patch('capo.heartbeat.evidence',return_value=[]) as read,patch('capo.heartbeat.select',return_value=payload):
                manager.tick(now)
                for worker in manager.workers.values():worker.join(5)
                manager.tick(now)
                client.chat_postMessage.assert_not_called()
                afternoon=now.replace(hour=15)
                manager.tick(afternoon)
                for worker in manager.workers.values():worker.join(5)
                manager.tick(afternoon);manager.tick(afternoon)
            self.assertEqual(read.call_count,2)
            client.chat_postMessage.assert_called_once()
