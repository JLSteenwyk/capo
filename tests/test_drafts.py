import base64
import tempfile
import unittest
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path
from unittest.mock import Mock

from capo.drafts import DraftTools
from capo.gmail import GmailReadTools


class DraftTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.home=Path(tmp.name)
        self.client=Mock();self.mail=GmailReadTools(self.client)
        self.tools=DraftTools(self.client,self.mail,self.home,'owner',{'message':'Draft to friend@example.com'})
        self.args=dict(id='',revision='',to=['friend@example.com'],cc=[],bcc=[],subject='Meeting',body='Hi, see you Friday.',reply_to_message='',attachment_ids=[])
        self.client.draft_write.return_value={'id':'draft1','message':{'threadId':'thread1'}}

    def raw_draft(self):
        m=EmailMessage();m['To']='friend@example.com';m['Subject']='Meeting';m.set_content('Original')
        m.add_attachment(b'file contents',maintype='text',subtype='plain',filename='notes.txt')
        return {'id':'draft1','message':{'threadId':'thread1','raw':base64.urlsafe_b64encode(m.as_bytes()).decode()}}

    def test_new_draft_verified_recipient_and_repeated_call(self):
        result=self.tools.save(**self.args,operation_id='event')
        self.assertFalse(result['sent'])
        self.assertEqual(result,self.tools.save(**self.args,operation_id='event'))
        self.client.draft_write.assert_called_once()
        with self.assertRaises(ValueError):self.tools.save(**dict(self.args,to=['guessed@example.org']),operation_id='other')
        with self.assertRaises(ValueError):self.tools.save(**dict(self.args,to=['friend@example.com\nBcc: other@example.org']),operation_id='inject')

    def test_edit_preserves_attachment_and_rejects_stale_revision(self):
        self.tools.known.add('draft1');self.client.get.return_value=self.raw_draft()
        found=self.tools.read('draft1')
        args=dict(self.args,id='draft1',revision=found['revision'],attachment_ids=[found['attachments'][0]['id']])
        result=self.tools.save(**args,operation_id='update')
        sent=self.client.draft_write.call_args.args[2]
        message=BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(sent['message']['raw']))
        self.assertEqual(next(message.iter_attachments()).get_payload(decode=True),b'file contents')
        self.assertEqual(sent['message']['threadId'],'thread1')
        self.client.get.side_effect=AssertionError('replay must not read mutated draft')
        self.assertEqual(self.tools.save(**args,operation_id='update'),result)
        self.client.get.side_effect=None
        with self.assertRaises(ValueError):self.tools.save(**dict(args,revision='stale'),operation_id='stale')
        with self.assertRaises(ValueError):self.tools.save(**dict(args,attachment_ids=['unknown']),operation_id='bad-file')

    def test_reply_thread_uses_original_headers(self):
        self.mail.known_ids.add('message1')
        self.client.get.return_value={'threadId':'thread1','payload':{'headers':[
            {'name':'From','value':'sender@example.org'},{'name':'Message-ID','value':'<original@example.org>'},
            {'name':'References','value':'<earlier@example.org>'},{'name':'Subject','value':'Original subject'}]}}
        args=dict(self.args,to=['sender@example.org'],reply_to_message='message1',subject='Original subject')
        self.tools.save(**args,operation_id='reply')
        sent=self.client.draft_write.call_args.args[2]
        m=BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(sent['message']['raw']))
        self.assertEqual(m['In-Reply-To'],'<original@example.org>')
        self.assertEqual(sent['message']['threadId'],'thread1')
        self.assertIn('<earlier@example.org>',m['References'])
        with self.assertRaises(ValueError):self.tools.save(**dict(args,subject='Wrong thread'),operation_id='wrong')

    def test_delete_replay_does_not_need_deleted_draft(self):
        self.tools.known.add('draft1');self.client.get.return_value=self.raw_draft()
        revision=self.tools.read('draft1')['revision']
        result=self.tools.delete('draft1',revision,'delete')
        self.client.get.side_effect=AssertionError('deleted draft no longer readable')
        self.assertEqual(self.tools.delete('draft1',revision,'delete'),result)
        self.client.draft_write.assert_called_once_with('DELETE','draft1')

    def test_source_attachment_reference_and_thread_read(self):
        raw=self.raw_draft()['message']['raw']
        self.mail.known_ids.add('message1');self.client.get.return_value={'raw':raw}
        attachment=self.tools.source_attachments('message1')['attachments'][0]
        self.tools.save(**dict(self.args,attachment_ids=[attachment['id']]),operation_id='attachment')
        self.assertEqual(self.client.draft_write.call_count,1)
        self.mail.known_threads.add('thread1')
        self.client.get.side_effect=[{'messages':[{'id':'message1'}]}, {'id':'message1','threadId':'thread1','payload':{'headers':[]}}]
        result=self.mail.thread('thread1')
        self.assertEqual(result['pages'][0]['messages'][0]['id'],'message1')
        with self.assertRaises(ValueError):self.mail.thread('not-observed')

    def test_email_deadline_composes_with_task_and_reply_draft(self):
        import json
        from capo.tasks import Tasks
        from capo.research_tools import ReadTools, research
        from test_tasks import fields
        def step(name,args):
            return {'action':'tool','tool':name,'arguments_json':json.dumps(args),
                    'reply':'','document_title':'','document':''}
        headers=[{'name':'From','value':'friend@example.com'}, {'name':'Subject','value':'Meeting'},
                 {'name':'Message-ID','value':'<source@example.com>'}]
        self.client.get.side_effect=[{'messages':[{'id':'m1','threadId':'t1'}]},
            {'id':'m1','threadId':'t1','payload':{'headers':headers,'mimeType':'text/plain',
                'body':{'data':base64.urlsafe_b64encode(b'Please send the outline by Friday.').decode()}}},
            {'id':'m1','threadId':'t1','payload':{'headers':headers}}]
        tasks=Tasks(self.home,'owner')
        registry=ReadTools(list(self.mail.tools.values())+self.tools.tools()+tasks.tools())
        provider=Mock();provider.call.side_effect=[
            step('mail.search',{'query':'subject:Meeting','page_size':'1','page_token':''}),
            step('mail.read',{'ids':['m1'],'strip_quotes':False}),
            step('tasks.save',{'id':'','expected_revision':'','fields':fields(title='Send outline',
                due_at='2026-09-18T17:00:00-07:00',sources=['gmail:m1'])}),
            step('mail.drafts.save',dict(self.args,reply_to_message='m1')),
            {'action':'finish','tool':'','arguments_json':'{}','reply':'Task saved and reply drafted.',
             'document_title':'','document':''}]
        result=research(provider,registry,{'message':'Track the email deadline and draft my reply.'},self.home/'composition')
        self.assertTrue(all('result' in r for r in result['receipts']))
        self.assertEqual(tasks.search()['tasks'][0]['sources'],['gmail:m1'])
        self.client.draft_write.assert_called_once()

    def test_timeout_recovery_finds_exact_draft_without_another_write(self):
        import hashlib
        self.client.draft_write.side_effect=TimeoutError()
        with self.assertRaises(TimeoutError):self.tools.save(**self.args,operation_id='uncertain')
        restored=DraftTools(self.client,self.mail,self.home,'owner',{'message':'Draft to friend@example.com'})
        action=restored.pending()['actions'][0]['id']
        m=EmailMessage();m['To']='friend@example.com';m['Subject']=self.args['subject']
        m['Message-ID']='<rewritten@example.com>'
        m['X-Capo-Action']='capo-'+hashlib.sha256(b'uncertain').hexdigest()+'@capo.invalid'
        m.set_content(self.args['body'])
        data={'message':{'raw':base64.urlsafe_b64encode(m.as_bytes()).decode(),'threadId':'thread1'}}
        self.client.get.side_effect=[{'drafts':[{'id':'draft1'}]}, {'message':{'payload':{'headers':[{'name':'X-Capo-Action','value':str(m['X-Capo-Action'])}]}}},data]
        result=restored.reconcile(action)
        self.assertTrue(result['saved']);self.assertTrue(result['reconciled'])
        self.assertEqual(restored.save(**self.args,operation_id='uncertain'),result)
        self.assertEqual(self.client.draft_write.call_count,1)

    def test_missing_reconciliation_evidence_never_repeats_write(self):
        self.client.draft_write.side_effect=TimeoutError()
        with self.assertRaises(TimeoutError):self.tools.save(**self.args,operation_id='uncertain')
        action=self.tools.pending()['actions'][0]['id']
        self.client.get.return_value={'drafts':[]}
        self.assertFalse(self.tools.reconcile(action)['confirmed'])
        self.assertEqual(len(self.tools.pending()['actions']),1)
        self.assertEqual(self.client.draft_write.call_count,1)

    def test_missing_credentials_fail_before_action_is_reserved(self):
        self.client.ready.side_effect=RuntimeError('Reconnect required')
        with self.assertRaises(RuntimeError):self.tools.save(**self.args,operation_id='auth')
        self.assertEqual(self.tools.pending()['actions'],[])
        self.client.draft_write.assert_not_called()
