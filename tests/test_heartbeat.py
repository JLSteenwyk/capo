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
