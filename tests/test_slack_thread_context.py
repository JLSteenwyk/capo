import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from capo.store import Store
from capo.slack_thread_context import recover, ThreadContextError
from capo.slack_images import context


class ThreadRecoveryTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        store=Store(Path(temp.name));self.addCleanup(store.db.close)
        self.service=SimpleNamespace(store=store,config={'team_id':'T123','channel_id':'C123','owner_user_id':'U123'},client=Mock())
        self.body={'team_id':'T123','event':{'type':'message','user':'U123','channel':'C123','ts':'105','thread_ts':'100','text':'Did you create it?'}}
        self.root={'user':'U123','ts':'100','text':'Create a recurring birthday event','files':[{'id':'F123','mimetype':'image/png'}]}

    def test_missing_root_recovered_as_context_not_pending_work(self):
        self.service.client.conversations_replies.return_value={'messages':[self.root,
            {'user':'UOTHER','ts':'101','thread_ts':'100','text':'untrusted'},
            {'user':'U123','ts':'102','thread_ts':'999','text':'different thread'},
            {'user':'U123','ts':'106','thread_ts':'100','text':'future'}]}
        recover(self.service,self.body)
        rows=list(self.service.store.db.execute('SELECT data,handled FROM slack_inbox'))
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['handled'],1)
        self.assertEqual(json.loads(rows[0]['data'])['event']['files'],self.root['files'])
        self.assertEqual(self.service.store.pending_slack(),[])
        recover(self.service,self.body)
        self.service.client.conversations_replies.assert_called_once()

    def test_recovered_image_reaches_existing_visual_pipeline(self):
        self.service.client.conversations_replies.return_value={'messages':[self.root]}
        import hashlib
        image=Mock();image.root=self.service.store.home/'images'
        directory=image.root/hashlib.sha256(b'followup').hexdigest()
        directory.mkdir(parents=True);(directory/'observation.json').write_text('{}')
        image.poll.return_value={'reply':'Birthday on October 5.'}
        self.service.image_conversation=image
        result=context(self.service,'followup',self.body,'Did you create it?')
        self.assertIn('Birthday on October 5',result)
        self.assertEqual(image.poll.call_args.args[1]['files'],['F123'])

    def test_text_only_root_recovered_and_history_failure_explicit(self):
        self.root.pop('files')
        self.service.client.conversations_replies.return_value={'messages':[self.root]}
        self.assertEqual(context(self.service,'text',self.body,'Follow up'),'Follow up')
        self.service.store.db.execute('DELETE FROM slack_inbox')
        self.body['event']['ts']='107'
        self.service.client.conversations_replies.side_effect=RuntimeError('private upstream details')
        with self.assertRaisesRegex(ThreadContextError,'earlier messages'):recover(self.service,self.body)
        self.assertEqual(self.service.store.pending_slack(),[])

    def test_bot_root_history_read_is_not_repeated_during_polling(self):
        self.service.client.conversations_replies.return_value={'messages':[dict(self.root,user='UBOT',bot_id='B123')]}
        recover(self.service,self.body);recover(self.service,self.body)
        self.service.client.conversations_replies.assert_called_once()
        self.assertEqual(self.service.store.pending_slack(),[])

    def test_transient_history_read_retries_once(self):
        self.service.client.conversations_replies.side_effect=[TimeoutError(),{'messages':[self.root]}]
        recover(self.service,self.body)
        self.assertEqual(self.service.client.conversations_replies.call_count,2)
        self.assertEqual(self.service.store.pending_slack(),[])

    def test_unauthorized_request_never_reads_slack(self):
        self.body['event']['user']='UOTHER'
        with self.assertRaises(ValueError):recover(self.service,self.body)
        self.service.client.conversations_replies.assert_not_called()
