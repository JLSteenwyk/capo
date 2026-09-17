import base64
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock,MagicMock,patch
from types import SimpleNamespace

from capo.slack_images import download,trusted_url,image_type,ImageError,SlackRedirects,context
from capo.providers import Providers
from capo.contracts import object_schema,TEXT
from capo.conversation import _write,ConversationPending
from capo.store import Store


class ImageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.png=b'\x89PNG\r\n\x1a\nexample'

    def test_provider_uses_real_image_blocks_without_enabling_tools(self):
        result={'type':'result','subtype':'success','structured_output':{'answer':'A diagram'}}
        with patch('capo.providers.run_process',return_value=json.dumps({'type':'system'})+'\n'+json.dumps(result)) as run:
            value=Providers(config={}, capacity=False).call('claude','Describe it',object_schema({'answer':TEXT}),self.root,self.root/'artifacts',images=[{'media_type':'image/png','data':base64.b64encode(self.png).decode()}])
        self.assertEqual(value,{'answer':'A diagram'})
        args=run.call_args.args
        self.assertEqual(args[0][args[0].index('--tools')+1],'')
        self.assertIn('--strict-mcp-config',args[0])
        self.assertEqual(json.loads(args[0][args[0].index('--settings')+1]),
                         {'autoMemoryEnabled':False,'disableAllHooks':True})
        self.assertIn('--no-session-persistence',args[0])
        self.assertEqual(args[0][args[0].index('--output-format')+1],'stream-json')
        blocks=json.loads(args[4])['message']['content']
        self.assertEqual(blocks[1]['type'],'image')
        self.assertEqual(base64.b64decode(blocks[1]['source']['data']),self.png)

    def test_expired_login_is_reported_as_authentication_failure(self):
        from capo.providers import AuthenticationError
        result={'type':'result','subtype':'success','is_error':True,
                'result':'Failed to authenticate: OAuth session expired and could not be refreshed'}
        with patch('capo.providers.run_process',return_value=json.dumps(result)):
            with self.assertRaisesRegex(AuthenticationError,'login has expired'):
                Providers(config={}, capacity=False).call('claude','Describe it',object_schema({'answer':TEXT}),
                    self.root,self.root/'expired',images=[{'media_type':'image/png',
                    'data':base64.b64encode(self.png).decode()}])

    def test_download_only_uses_trusted_slack_url(self):
        client=Mock();client.files_info.return_value={'file':{'size':len(self.png),'mimetype':'image/png','url_private':'https://files.slack.com/files-pri/example/image.png'}}
        response=Mock();response.read.return_value=self.png
        opener=MagicMock();opener.open.return_value.__enter__.return_value=response
        with patch.dict(os.environ,{'SLACK_BOT_TOKEN':'synthetic-test-token'}),patch('capo.slack_images.build_opener',return_value=opener):
            result=download(client,'F123')
        self.assertEqual(result['media_type'],'image/png')
        request=opener.open.call_args.args[0]
        self.assertEqual(request.get_header('Authorization'),'Bearer synthetic-test-token')
        self.assertEqual(response.read.call_args.args[0],4_000_001)

    def test_untrusted_redirect_and_large_or_nonimage_file_rejected(self):
        for url in ('http://files.slack.com/image','https://files.slack.com.evil.example/image','https://localhost/a','https://user@files.slack.com/a'):
            with self.assertRaises(ImageError):trusted_url(url)
        with self.assertRaises(ImageError):
            SlackRedirects().redirect_request(None,None,302,'',{},'https://evil.example/file')
        with self.assertRaises(ImageError):image_type(b'<html>Sign in</html>')
        client=Mock();client.files_info.return_value={'file':{'size':4_000_001,'mimetype':'image/png'}}
        with self.assertRaises(ImageError):download(client,'F123')

    def test_missing_scope_has_actionable_message(self):
        client=Mock();error=RuntimeError();error.response={'error':'missing_scope'}
        client.files_info.side_effect=error
        with self.assertRaisesRegex(ImageError,'files:read'):download(client,'F123')

    def test_owner_thread_image_persists_and_other_threads_are_excluded(self):
        store=Store(self.root/'state');self.addCleanup(store.db.close)
        config={'team_id':'T123','channel_id':'C123','owner_user_id':'U123'}
        prior={'team_id':'T123','event_id':'old','event':{'type':'app_mention','user':'U123','channel':'C123','ts':'100.1','text':'look','files':[{'id':'F123','mimetype':'image/png'}]}}
        store.enqueue_slack('old',prior)
        body={'team_id':'T123','event':{'type':'message','user':'U123','channel':'C123','ts':'101.1','thread_ts':'100.1','text':'Use that image'}}
        service=SimpleNamespace(store=store,config=config,client=Mock())
        from capo.slack_images import ImageConversation
        with patch('capo.slack_images.download',return_value={'media_type':'image/png','data':base64.b64encode(self.png).decode()}),patch('capo.slack_images.Providers') as provider:
            provider.return_value.call.return_value={'observations':'A reservation at 6:15 pm.','uncertainties':'End time not shown.'}
            for _ in range(300):
                try:
                    result=context(service,'new',body,'Use that image');break
                except ConversationPending:time.sleep(.005)
            else:self.fail('Image analysis did not finish')
            self.assertIn('6:15 pm',result)
            self.assertIn('End time',result)
            provider.return_value.call.assert_called_once()
            self.assertIn('6:15 pm',context(service,'new',body,'Use that image'))
        other=json.loads(json.dumps(body));other['event'].update(type='app_mention',thread_ts='999.1')
        self.assertEqual(context(service,'other',other,'Another request'),'Another request')
        other['event']['user']='UOTHER'
        with self.assertRaises(ValueError):context(service,'bad',other,'Other user')
