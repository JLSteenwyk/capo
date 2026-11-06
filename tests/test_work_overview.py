import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from capo.work_overview import WorkOverview
from capo.capabilities import owner_key
from capo.tasks import Tasks
from capo.store import Store
from capo.effects import Effects
from test_tasks import fields

CONFIG={'team_id':'T1','channel_id':'C1','owner_user_id':'U1'}


class OverviewTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.home=Path(tmp.name)
        self.owner=owner_key(CONFIG);self.tasks=Tasks(self.home,self.owner)

    def request(self,id,status,items=None,handled=True,thread='100'):
        store=Store(self.home)
        try:store.enqueue_slack(id,{'team_id':'T1','event':{'user':'U1','channel':'C1','ts':'100','thread_ts':thread,'text':'Synthetic request'}});store.finish_slack(id) if handled else None
        finally:store.db.close()
        root=self.home/'capabilities'/hashlib.sha256(self.owner.encode()).hexdigest()/'conversation'/hashlib.sha256(id.encode()).hexdigest();root.mkdir(parents=True)
        (root/'outcome.json').write_text(json.dumps({'status':status}))
        (root/'checkpoint.json').write_text(json.dumps({'result':{'status':status,'outcome_report':{'outcomes':items or []}}}))

    def test_projection_reuses_tasks_and_keeps_completion_evidence(self):
        self.tasks.save('','',fields(title='Tracked reply'), 'save')
        self.request('waiting','partial',[{'status':'needs_input','next_step':'Which date?'}])
        overview=WorkOverview(self.home,CONFIG).read(refresh=False)
        self.assertEqual(len(overview['groups']['tracked']),1)
        self.assertEqual(overview['groups']['tracked'][0]['source_check']['status'],'not_checked')
        self.assertEqual(overview['groups']['waiting_for_owner'][0]['next_step'],'Which date?')
        self.assertIn('app.slack.com',overview['groups']['waiting_for_owner'][0]['url'])
        self.assertEqual(len(self.tasks.search()['tasks']),1)

    def test_newer_turn_supersedes_old_question_and_handoff_is_explicit(self):
        self.request('old','partial',[{'status':'needs_input','next_step':'Which date?'}])
        self.request('new','reported_complete',[{'status':'complete','kind':'handoff'}])
        overview=WorkOverview(self.home,CONFIG).read(refresh=False)
        self.assertEqual(overview['groups']['waiting_for_owner'],[])
        row=overview['groups']['completed'][0]
        self.assertTrue(row['reply_delivered'])
        self.assertEqual(row['outcomes'][0]['kind'],'handoff')
        self.assertTrue(overview['groups']['tracked'][0]['superseded_by_later_turn'])

    def test_uncertain_action_has_reconciliation_next_step_and_owner_scope(self):
        effects=Effects(self.home,self.owner)
        def lost():raise TimeoutError()
        with self.assertRaises(TimeoutError):effects.run('effect',{'kind':'calendar-change','plan':{}},lost)
        row=WorkOverview(self.home,CONFIG).read(refresh=False)['groups']['uncertain'][0]
        self.assertIn('reconcile',row['next_step'])
        other=dict(CONFIG,owner_user_id='U2')
        self.assertEqual(WorkOverview(self.home,other).read(refresh=False)['groups']['uncertain'],[])

    def test_fresh_source_checks_are_attached_without_mutating_saved_status(self):
        task=self.tasks.save('','',fields(), 'save')['task']
        evidence=Mock();evidence.refresh.return_value={'tasks':[{'task_id':task['id'],'status':'already_replied'}]}
        rows=WorkOverview(self.home,CONFIG,evidence).read()['groups']['tracked']
        self.assertEqual(rows[0]['source_check']['status'],'already_replied')
        self.assertEqual(self.tasks.get(task['id'])['status'],'open')
