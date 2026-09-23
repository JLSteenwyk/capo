import hashlib
import json
import tempfile
import unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path

from capo.store import Store
from capo.capabilities import owner_key
from capo.reliability import review,classify
from capo.conversation import _write
from capo.report_freshness import snapshot,guard


class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.home=Path(self.tmp.name);self.store=Store(self.home);self.addCleanup(self.store.db.close)
        self.config={'team_id':'TTEST','channel_id':'CTEST','owner_user_id':'UTEST'}
        self.now=datetime.now(timezone.utc)

    def request(self,key,age=1200,handled=0,thread=None,user='UTEST',result=None):
        ts=str(self.now.timestamp()-age)
        body={'team_id':'TTEST','event':{'channel':'CTEST','user':user,'ts':ts,'thread_ts':thread or ts,'text':'private request'}}
        with self.store.db:self.store.db.execute('INSERT INTO slack_inbox VALUES (?,?,?)',(key,json.dumps(body),handled))
        root=self.home/'capabilities'/hashlib.sha256(owner_key(self.config).encode()).hexdigest()/'conversation'/hashlib.sha256(key.encode()).hexdigest()
        root.mkdir(parents=True,exist_ok=True)
        if result:_write(root/'checkpoint.json',{'result':result})
        return root,ts

    def test_pending_partial_waiting_and_owner_scope(self):
        self.request('pending')
        self.request('young',age=10)
        self.request('foreign',user='UOTHER')
        self.request('partial',handled=1,result={'status':'partial'})
        self.request('question',handled=1,result={'status':'partial','outcome_report':{'outcomes':[{'status':'needs_input'}]}})
        # Each request belongs to a distinct thread in this fixture.
        with self.store.db:
            for key,data in self.store.db.execute('SELECT id,data FROM slack_inbox').fetchall():
                value=json.loads(data);value['event']['thread_ts']=value['event']['ts']+key
                self.store.db.execute('UPDATE slack_inbox SET data=? WHERE id=?',(json.dumps(value),key))
        result=review(self.home,self.config,self.now)
        self.assertEqual({r['status'] for r in result['items']},{'request_pending','incomplete_outcome'})
        self.assertEqual(len(result['items']),2)
        self.assertNotIn('private request',json.dumps(result))
        self.store.finish_slack('pending')
        self.assertEqual(len(review(self.home,self.config,self.now)['items']),1)

    def test_newer_completed_turn_supersedes_old_partial_and_cancel_is_quiet(self):
        root,thread=self.request('old',handled=1,result={'status':'partial'})
        self.request('new',age=2,handled=1,thread=thread,result={'status':'reported_complete'})
        root,_=self.request('cancel',age=1800);_write(root/'cancelled.json',{})
        self.assertEqual(review(self.home,self.config,self.now)['items'],[])

    def test_ready_reply_is_distinguished_and_malformed_foreign_rows_ignored(self):
        self.request('delivery')
        self.store.db.execute('CREATE TABLE slack_deliveries(event_id TEXT,data TEXT)')
        self.store.db.execute('INSERT INTO slack_deliveries VALUES (?,?)',('delivery','{}'));self.store.db.commit()
        self.assertEqual(review(self.home,self.config,self.now)['items'][0]['status'],'delivery_pending')
        self.store.db.execute("INSERT INTO slack_inbox VALUES ('bad','not json',0)");self.store.db.commit()
        self.assertEqual(len(review(self.home,self.config,self.now)['items']),1)

    def test_freshness_never_replays_effects_or_uncertain_delivery(self):
        for status in ('sending','sent'):
            run={'status':status,'evidence_snapshot':snapshot(0,rebuildable=True)}
            self.assertTrue(guard(run,1000));self.assertEqual(run['status'],status)
        run={'status':'ready','attempts':1,'payload':{'text':'old'},'wire_text':'old','evidence_snapshot':snapshot(0,rebuildable=True)}
        self.assertFalse(guard(run,1000));self.assertEqual(run['status'],'queued');self.assertNotIn('payload',run);self.assertNotIn('wire_text',run)
        for value in (snapshot(0),snapshot(2000),snapshot(float('nan')),snapshot(0,rebuildable=True)):
            run={'status':'ready','attempts':3,'evidence_snapshot':value}
            self.assertFalse(guard(run,1000));self.assertEqual(run['status'],'failed')
        self.assertTrue(guard({'status':'ready','evidence_snapshot':snapshot(900)},1000))

    def test_source_specific_report_ages_and_historical_mutations(self):
        from capo.report_freshness import from_receipts
        from capo.research_tools import ReadTool,ReadTools
        from capo.contracts import object_schema
        tools=ReadTools([ReadTool('mail.thread','Read',object_schema({}),lambda:None,fresh_for=300),
                         ReadTool('github.inspect','Read',object_schema({}),lambda:None,fresh_for=900),
                         ReadTool('calendar.change','Write',object_schema({}),lambda:None,mutates=True)])
        receipts=[{'tool':'calendar.change','result':{'created':True},'observed_at':1},
                  {'tool':'github.inspect','result':{},'observed_at':200}]
        value=from_receipts(receipts,tools,1000)
        self.assertTrue(guard({'status':'ready','evidence_snapshot':value},1000))
        receipts.append({'tool':'mail.thread','result':{},'observed_at':600})
        run={'status':'ready','evidence_snapshot':from_receipts(receipts,tools,1000)}
        self.assertFalse(guard(run,1000));self.assertEqual(run['status'],'failed')
