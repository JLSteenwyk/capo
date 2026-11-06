import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from capo.slack_recovery import RecoveryScan,BackgroundRecovery
from capo.slack import ingest
from capo.store import Store

CONFIG={'team_id':'T1','channel_id':'C1','owner_user_id':'UOWNER'}
NOW=1900000000


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.home=Path(tmp.name)
        self.client=Mock();self.client.conversations_history.return_value={'messages':[]}
        self.client.conversations_replies.return_value={'messages':[]}
        self.scan=RecoveryScan(self.home,CONFIG,self.client)

    def seed(self,ts,text='Received',thread=None):
        event=dict(type='message',user='UOWNER',channel='C1',ts=str(ts),text=text)
        if thread:event['thread_ts']=thread
        return ingest(self.home,CONFIG,{'team_id':'T1','event_id':'live:'+str(ts),'event':event})

    def pending(self):
        store=Store(self.home)
        try:return store.pending_slack()
        finally:store.db.close()

    def test_old_thread_and_unrelated_bot_reply_do_not_hide_request(self):
        root=str(NOW-30*86400);self.seed(root)
        self.scan.step(NOW)
        self.client.conversations_replies.return_value={'messages':[
            {'ts':str(NOW-10),'user':'UOWNER','text':'Do additional work','thread_ts':root},
            {'ts':str(NOW-5),'user':'UBOT','text':'Earlier work finished','bot_id':'B1'}]}
        self.assertEqual(self.scan.step(NOW+1),1)
        self.assertEqual(self.pending()[-1][1]['event']['thread_ts'],root)
        self.assertFalse(self.seed(NOW-10,thread=root))
        self.scan.step(NOW+2);self.assertEqual(self.scan.step(NOW+3),0)

    def test_history_pagination_survives_restart_and_discovers_ancient_roots(self):
        self.client.conversations_history.return_value={'messages':[], 'response_metadata':{'next_cursor':'page2'}}
        self.scan.step(NOW)
        self.client.conversations_history.return_value={'messages':[{'ts':str(NOW-100*86400),'reply_count':1}]}
        RecoveryScan(self.home,CONFIG,self.client).step(NOW+1)
        self.assertEqual(self.client.conversations_history.call_args.kwargs['cursor'],'page2')
        state=json.loads(self.scan.path.read_text())
        self.assertIn(str(NOW-100*86400),state['threads'])
        self.assertTrue(state['coverage']['historical_roots_complete'])

    def test_outage_watermark_is_not_reset_to_one_day(self):
        self.scan.step(NOW)
        self.client.conversations_history.return_value={'messages':[{'ts':str(NOW+50),'user':'UOWNER','text':'Missed during outage'}]}
        self.assertEqual(self.scan.step(NOW+3*86400),1)
        self.assertLess(float(self.client.conversations_history.call_args.kwargs['oldest']),NOW+50)

    def test_failed_read_does_not_advance_cursor_and_no_live_work_on_worker_thread(self):
        self.client.conversations_history.side_effect=TimeoutError()
        with self.assertRaises(TimeoutError):self.scan.step(NOW)
        self.assertEqual(json.loads(self.scan.path.read_text())['bootstrap_oldest'],NOW-86400)
        worker=BackgroundRecovery(self.home,CONFIG,self.client,lambda:True)
        def blocked():worker.stop.set();raise TimeoutError()
        worker.scan.step=blocked;worker.run()
        self.assertTrue(worker.scan.path.with_suffix('.error.json').exists())

    def test_thread_pagination_and_original_timestamp_preserved(self):
        root=str(NOW-30*86400);self.seed(root);self.scan.step(NOW)
        self.client.conversations_replies.return_value={'messages':[], 'response_metadata':{'next_cursor':'more'}}
        self.scan.step(NOW+1);self.scan.step(NOW+2)
        self.client.conversations_replies.return_value={'messages':[{'ts':str(NOW-20),'user':'UOWNER','text':'Followup'}]}
        self.assertEqual(self.scan.step(NOW+3),1)
        self.assertEqual(self.client.conversations_replies.call_args.kwargs['cursor'],'more')
        self.assertEqual(self.pending()[-1][1]['event']['ts'],str(NOW-20))

    def test_background_start_returns_while_api_read_is_blocked(self):
        import threading
        entered=threading.Event();release=threading.Event()
        worker=BackgroundRecovery(self.home,CONFIG,self.client,lambda:True)
        def read():entered.set();release.wait(3)
        worker.scan.step=read
        worker.start()
        try:
            self.assertTrue(entered.wait(1))
            # Independent live ingestion remains usable while history is blocked.
            self.assertTrue(self.seed(NOW))
            self.assertEqual(len(self.pending()),1)
        finally:
            release.set();worker.close()
        self.assertFalse(worker.thread.is_alive())

    def test_live_recovery_race_queues_exactly_one_owner_request(self):
        from concurrent.futures import ThreadPoolExecutor
        import threading
        gate=threading.Barrier(2)
        self.client.conversations_history.return_value={'messages':[{'ts':str(NOW),'user':'UOWNER','text':'Received'}]}
        def recover():gate.wait();return self.scan.step(NOW+1)
        def live():gate.wait();return self.seed(NOW)
        with ThreadPoolExecutor(max_workers=2) as pool:
            a=pool.submit(recover);b=pool.submit(live);a.result();b.result()
        self.assertEqual(len(self.pending()),1)
