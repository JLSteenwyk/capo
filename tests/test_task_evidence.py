import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from capo.task_evidence import TaskEvidence
from capo.tasks import Tasks
from test_tasks import fields


def message(id, at, sent=False, draft=False):
    return {'id':id,'threadId':'thread','internalDate':str(at),
            'labelIds':(['SENT'] if sent else [])+(['DRAFT'] if draft else []),
            'payload':{'headers':[]}, 'snippet':'Example message'}


class TaskEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.home=Path(self.tmp.name)
        self.tasks=Tasks(self.home,'owner')
        self.tool=TaskEvidence(self.home,'owner',{'gmail':{'enabled':True}})
        self.tool.mail=Mock()

    def task(self,sources=None):
        return self.tasks.save('','',fields(sources=sources or ['email:abc']),'task')['task']

    def configure(self,messages):
        self.tool.mail.get.side_effect=lambda path,params: {'messages':messages} if path.startswith('threads/') else message('abc',10)

    def test_old_linked_mail_is_checked_without_recent_sent_search(self):
        task=self.task();self.configure([message('abc',10),message('reply',20,True)])
        result=self.tool.refresh([task['id']])['tasks'][0]
        self.assertEqual(result['sources'][0]['later_sent_ids'],['reply'])
        self.assertEqual(self.tasks.get(task['id'])['status'],'open')
        self.assertTrue(all(call.args[0].startswith(('messages/','threads/')) for call in self.tool.mail.get.call_args_list))
        observation=self.tool.observations({'tasks':[result]})[0]
        self.assertEqual(observation['message_id'],'abc')
        self.assertEqual(observation['linked_task_id'],task['id'])

    def test_overview_does_not_reuse_stale_thread_snapshot(self):
        task=self.task();self.configure([message('abc',10)])
        first=self.tool.overview()['groups']['unscheduled'][0]
        self.assertEqual(first['source_check']['sources'][0]['later_sent_ids'],[])
        self.configure([message('abc',10),message('reply',20,True),message('followup',30)])
        second=self.tool.overview()['groups']['unscheduled'][0]
        self.assertEqual(second['source_check']['sources'][0]['later_sent_ids'],['reply'])
        self.assertFalse(second['source_check']['sources'][0]['messages'][-1]['sent_by_owner'])
        self.assertEqual(second['revision'],task['revision'])

    def test_draft_and_earlier_sent_message_do_not_prove_reply(self):
        task=self.task();self.configure([message('prior',1,True),message('abc',10),message('draft',20,True,True)])
        self.assertEqual(self.tool.refresh([task['id']])['tasks'][0]['sources'][0]['later_sent_ids'],[])

    def test_grouped_task_checks_every_reference_and_marks_partial_coverage(self):
        task=self.task(['email:abc','email:def','calendar:example'])
        self.configure([message('abc',10),message('reply',20,True)])
        result=self.tool.refresh([task['id']])['tasks'][0]
        self.assertEqual(result['status'],'unverified')
        self.assertEqual(len(result['sources']),3)
        self.assertEqual(result['sources'][-1]['status'],'unsupported')
        self.assertEqual(len([c for c in self.tool.mail.get.call_args_list if c.args[0].startswith('threads/')]),1)

    def test_unavailable_source_never_becomes_no_reply_claim(self):
        task=self.task();self.tool.mail.get.side_effect=RuntimeError('private service detail')
        result=self.tool.refresh([task['id']])['tasks'][0]
        self.assertEqual(result['status'],'unverified')
        self.assertNotIn('later_sent_ids',result['sources'][0])
        self.assertNotIn('private service detail',str(result))
        self.assertEqual(self.tool.observations({'tasks':[result]}),[])

    def test_closed_work_excluded_owner_isolation_and_budget(self):
        task=self.task()
        other=TaskEvidence(self.home,'other',{})
        with self.assertRaises(ValueError):other.refresh([task['id']])
        self.tasks.save(task['id'],'1',fields(status='paused',sources=task['sources']),'pause')
        self.assertEqual(self.tool.background()['tasks'],[])
        self.tool.mail.get.assert_not_called()
        with self.assertRaises(ValueError):self.tool.refresh(['abc']*21)

    def test_fresh_source_changes_reach_existing_monitor(self):
        from capo.observations import Observations
        task=self.task();obs=Observations(self.home,'owner')
        self.configure([message('abc',10)])
        first=self.tool.observations(self.tool.refresh([task['id']]))
        changed=obs.changed(first);obs.acknowledge(changed)
        self.assertEqual(obs.changed(self.tool.observations(self.tool.refresh([task['id']]))),[])
        self.configure([message('abc',10),message('reply',20,True)])
        self.assertEqual(len(obs.changed(self.tool.observations(self.tool.refresh([task['id']])))),1)

    def test_annotation_does_not_assert_an_already_sent_reply_is_owed(self):
        task=self.task();self.configure([message('abc',10),message('reply',20,True)])
        items=[{'task_id':task['id'],'status':'Due yesterday'}]
        self.tool.annotate(items,self.tool.refresh([task['id']]))
        self.assertIn('Later reply found',items[0]['status'])
        self.assertNotIn('Due yesterday',items[0]['status'])

    def test_refreshed_ids_can_be_read_with_shared_mail_tools(self):
        from capo.gmail import GmailReadTools
        task=self.task();self.configure([message('abc',10),message('reply',20,True)])
        self.tool.mail_reads=GmailReadTools(self.tool.mail)
        self.tool.refresh([task['id']])
        result=self.tool.mail_reads.thread('thread')
        self.assertEqual(result['status'],'complete')
        self.assertEqual(len(result['pages'][0]['messages']),2)
        with self.assertRaises(ValueError):self.tool.mail_reads.thread('invented')

    def test_monitor_reconciles_existing_task_from_fresh_source(self):
        from capo.monitoring import Monitor
        from capo.capabilities import owner_key
        from datetime import datetime, timezone
        tasks=Tasks(self.home,owner_key({}))
        task=tasks.save('','',fields(sources=['email:abc']),'tracked')['task']
        item={'kind':'email','id':'email:abc','message_id':'abc','linked_task_id':task['id'],
              'title':'Task thread updated','thread_evidence':{'later_sent_ids':['reply']}}
        def review(provider,tools,request,*args,**kwargs):
            current=tools.call('tasks.get',{'id':task['id']})
            ref=request['observations'][0]['source_reference']
            tools.call('commitments.observe',{'id':task['id'],'expected_revision':str(current['revision']),
                'fields':fields(status='completed',sources=[ref],notes='Fresh thread evidence confirms the requested reply.'),
                'evidence_refs':[ref],'certainty':'explicit'},operation_id='observed-reply')
            return {'reply':'','document':'','document_title':'','status':'complete'}
        with patch('capo.monitoring.research',side_effect=review):
            Monitor(self.home,{}).tick(datetime.now(timezone.utc),[item])
        self.assertEqual(tasks.get(task['id'])['status'],'completed')

    def test_partial_monitor_review_does_not_acknowledge_source_changes(self):
        from capo.monitoring import Monitor
        from datetime import datetime, timezone
        item={'kind':'email','id':'email:abc','message_id':'abc','linked_task_id':'example','title':'Updated'}
        monitor=Monitor(self.home,{})
        with patch('capo.monitoring.research',return_value={'status':'partial'}):
            monitor.tick(datetime.now(timezone.utc),[item])
        self.assertEqual(len(monitor.observations.changed([item])),1)
        self.assertIn('out of date',monitor.notices()[0]['summary'])
