import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from capo.bluebubbles import BlueBubbles, BridgeError
from capo.channel_sync import Journal, MirroredSlackClient
from capo.imessage import Bridge, settings, journal
from capo.store import Store


def config():
    return {'team_id':'T123','channel_id':'C123','owner_user_id':'U123','repositories':{},
            'imessage':{'enabled':True,'server_url':'http://127.0.0.1:1234',
                        'chat_guid':'iMessage;-;owner@example.invalid','owner_address':'owner@example.invalid'}}


def message(guid='m1', text='Move my appointment to 3 pm.'):
    return {'guid':guid,'text':text,'dateCreated':1900000000000,'isFromMe':False,
            'handle':{'address':'owner@example.invalid'},'chats':[{'guid':config()['imessage']['chat_guid']}],
            'attachments':[]}


class BridgeTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.home=Path(tmp.name)
        self.config=config();self.blue=Mock();self.slack=Mock()
        self.slack.auth_test.return_value={'team_id':'T123'}
        self.slack.chat_postMessage.return_value={'ts':'1900000001.001'}
        self.blue.chat.return_value={'guid':self.config['imessage']['chat_guid'],'participants':[{'address':'owner@example.invalid'}]}
        self.blue.send.return_value={'guid':'sent'}
        self.blue.messages.return_value=[]
        self.bridge=Bridge(self.home,self.config,self.blue,self.slack)

    def inbox(self):
        store=Store(self.home)
        try:return [json.loads(row[0]) for row in store.db.execute('SELECT data FROM slack_inbox')]
        finally:store.db.close()

    def test_threads_can_select_older_slack_context_without_reexecuting_it(self):
        store=Store(self.home)
        body={'team_id':'T123','event_id':'older','event':{'type':'app_mention','channel':'C123','user':'U123',
              'ts':'1800000000.001','text':'Prepare a trip.'}}
        store.enqueue_slack('older',body);store.db.close()
        self.bridge.incoming(message(text='threads'))
        rows=self.bridge.j.pending();self.assertIn('Prepare a trip.',rows[0]['text'])
        self.assertEqual(len(self.inbox()),1)
        code=self.bridge.j.code('1800000000.001')
        self.bridge.incoming(message('m2','switch '+code))
        self.assertEqual(self.bridge.j.get('active_thread'),'1800000000.001')
        self.slack.chat_postMessage.assert_not_called()

    def test_capture_storage_error_does_not_repeat_successful_slack_post(self):
        broken=Mock();broken.enqueue.side_effect=OSError('Disk unavailable')
        wrapper=MirroredSlackClient(self.slack,broken,'C123')
        with self.assertLogs('capo.channel_sync',level='ERROR'):
            self.assertEqual(wrapper.chat_postMessage(channel='C123',text='Done.'),{'ts':'1900000001.001'})
        self.slack.chat_postMessage.assert_called_once()

    def test_invalid_source_date_is_rejected_before_mirroring(self):
        for stamp in [None,'yesterday',float('nan'),0]:
            value=message();value['dateCreated']=stamp
            with self.assertRaises(BridgeError):self.bridge.incoming(value)
        self.slack.chat_postMessage.assert_not_called()

    def test_imessage_dispatch_uses_same_owner_memory_and_shared_loop(self):
        from capo.slack import SlackService, ingest
        from capo.capabilities import owner_key
        self.config['repositories']={'project':{'path':str(self.home),'checks':['python3 -c "print(1)"']}}
        original={'team_id':'T123','event_id':'original','event':{'type':'app_mention','channel':'C123','user':'U123',
                  'ts':'1800000000.001','text':'Make a plan for the trip.'}}
        ingest(self.home,self.config,original)
        self.bridge.j.put('active_thread','1800000000.001')
        self.bridge.incoming(message(text='Continue the same plan.'))
        row=self.inbox()[-1]
        store=Store(self.home)
        try:
            service=SlackService(store,self.config,self.slack)
            with patch('capo.capabilities.CapabilityConversation.poll',return_value={'reply':'Continuing.'}) as poll:
                self.assertEqual(service.dispatch(row['event_id'],row),'Continuing.')
                context=poll.call_args.args[1]
                self.assertEqual(context['request_thread'],'1800000000.001')
                self.assertEqual(context['owner_scope'],owner_key(self.config))
                self.assertEqual(context['owner_request']['text'],'Continue the same plan.')
        finally:store.db.close()

    def test_binding_requires_exact_owner_direct_chat_and_workspace(self):
        self.assertTrue(self.bridge.check()['direct_owner_chat'])
        self.blue.chat.return_value['participants'].append({'address':'other@example.invalid'})
        with self.assertRaises(ValueError):self.bridge.check()
        changed=copy.deepcopy(self.config);changed['imessage']['owner_address']='other@example.invalid'
        with self.assertRaises(ValueError):journal(self.home,changed)
        changed['imessage']['chat_guid']='iMessage;+;group'
        with self.assertRaises(ValueError):settings(changed)

    def test_incoming_echo_stranger_and_other_chat_are_never_executed(self):
        for changes in [{'isFromMe':True},{'handle':{'address':'other@example.invalid'}},
                        {'chats':[{'guid':'iMessage;+;group'}]},{'associatedMessageType':2000}]:
            value=message();value.update(changes);self.bridge.incoming(value)
        self.assertEqual(self.inbox(),[])
        self.slack.chat_postMessage.assert_not_called()

    def test_repeated_incoming_has_one_canonical_owner_event_and_ack(self):
        self.bridge.incoming(message());self.bridge.incoming(message())
        restarted=Bridge(self.home,self.config,self.blue,self.slack);restarted.incoming(message())
        rows=self.inbox();self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['event']['user'],'U123')
        self.assertEqual(rows[0]['event']['ts'],'1900000000.0')
        self.assertEqual(rows[0]['event']['thread_ts'],'1900000001.001')
        self.assertEqual(len(self.bridge.j.pending()),0)
        self.slack.chat_postMessage.assert_called_once()
        self.bridge.deliver();self.blue.send.assert_called_once()

    def test_switch_and_new_conversations_keep_stable_threads(self):
        code=self.bridge.j.code('1800000000.001')
        self.bridge.incoming(message(text='switch '+code))
        self.assertEqual(self.bridge.j.get('active_thread'),'1800000000.001')
        self.assertEqual(self.inbox(),[])
        self.bridge.incoming(message('m2',code+': Keep the time; change the room.'))
        self.assertEqual(self.inbox()[0]['event']['thread_ts'],'1800000000.001')
        self.slack.chat_postMessage.return_value={'ts':'1900000002.001'}
        self.bridge.incoming(message('m3','new: Prepare a packing list.'))
        self.assertEqual(self.inbox()[1]['event']['thread_ts'],'1900000002.001')

    def test_image_is_shared_in_slack_and_attached_to_same_request(self):
        self.blue.attachment.return_value=b'\x89PNG\r\n\x1a\nexample'
        self.slack.files_upload_v2.return_value={'files':[{'id':'F123'}]}
        value=message(text='Read this schedule.');value['attachments']=[{'guid':'image'}]
        self.bridge.incoming(value)
        row=self.inbox()[0]
        self.assertEqual(row['event']['files'][0]['id'],'F123')
        self.assertEqual(self.slack.files_upload_v2.call_args.kwargs['thread_ts'],row['event']['thread_ts'])
        self.bridge.incoming(value);self.slack.files_upload_v2.assert_called_once()

    def test_uncertain_upload_never_repeats_or_executes_missing_image(self):
        self.blue.attachment.return_value=b'\x89PNG\r\n\x1a\nexample'
        self.slack.files_upload_v2.side_effect=TimeoutError()
        value=message();value['attachments']=[{'guid':'image'}]
        with self.assertRaises(TimeoutError):self.bridge.incoming(value)
        with self.assertRaises(BridgeError):self.bridge.incoming(value)
        self.assertEqual(self.inbox(),[]);self.slack.files_upload_v2.assert_called_once()

    def test_uncertain_slack_mirror_never_repeats_or_executes(self):
        self.slack.chat_postMessage.side_effect=TimeoutError()
        with self.assertRaises(TimeoutError):self.bridge.incoming(message())
        with self.assertRaises(BridgeError):self.bridge.incoming(message())
        self.slack.chat_postMessage.assert_called_once();self.assertEqual(self.inbox(),[])

    def test_outbound_timeout_reconciles_without_second_send(self):
        self.bridge.j.enqueue('reply','thread','Capo: Saved your changes.')
        self.blue.send.side_effect=TimeoutError()
        with self.assertRaises(TimeoutError):self.bridge.deliver()
        row=self.bridge.j.pending()[0]
        self.blue.messages.return_value=[{'isFromMe':True,'text':row['text'],'guid':'delivered'}]
        self.bridge.deliver();self.assertEqual(self.bridge.j.pending(),[])
        self.blue.send.assert_called_once()

    def test_unconfirmed_outbound_stays_pending_without_blind_retry(self):
        self.bridge.j.enqueue('reply','thread','Capo: Saved.')
        self.blue.send.side_effect=TimeoutError()
        with self.assertRaises(TimeoutError):self.bridge.deliver()
        with self.assertRaises(BridgeError):self.bridge.deliver()
        self.blue.send.assert_called_once()

    def test_slack_owner_and_capabilities_replies_are_mirrored_without_execution(self):
        self.bridge.j.put('slack_cursor',0)
        from capo.slack import ingest
        event={'team_id':'T123','event_id':'E1','event':{'type':'app_mention','channel':'C123','user':'U123',
               'ts':'1800000000.001','text':'<@UBOT> Check my calendar.'}}
        self.assertTrue(ingest(self.home,self.config,event))
        self.bridge.collect_slack();self.bridge.collect_slack()
        client=MirroredSlackClient(self.slack,self.bridge.j,'C123')
        client.chat_postMessage(channel='C123',thread_ts='1800000000.001',text='All clear &amp; ready.')
        self.assertEqual(len(self.bridge.j.pending()),2)
        self.assertIn('All clear & ready.',self.bridge.j.pending()[1]['text'])
        self.assertEqual(len(self.inbox()),1)

    def test_long_messages_are_complete_and_duplicate_enqueue_is_idempotent(self):
        text='Long answer '*700
        self.bridge.j.enqueue('answer','thread',text);self.bridge.j.enqueue('answer','thread',text)
        rows=self.bridge.j.pending();reassembled=''.join(r['text'].split('] ',1)[1] for r in rows)
        self.assertEqual(reassembled,text)

    def test_first_poll_does_not_execute_historical_imessages(self):
        with patch('capo.imessage.time.time',return_value=1900000000):self.bridge.tick()
        self.assertEqual(self.blue.messages.call_args.args[1],1900000000000)
        self.assertEqual(self.inbox(),[])


class TransportTests(unittest.TestCase):
    def test_remote_plaintext_credentials_and_redirects_are_rejected(self):
        for url in ['http://example.invalid:1234','https://user:pass@example.invalid','https://example.invalid?password=x']:
            with self.assertRaises(ValueError):BlueBubbles(url,'secret')
        from capo.bluebubbles import NoRedirect
        with self.assertRaises(BridgeError):NoRedirect().redirect_request(None,None,None,None,None,None)

    def test_fixed_send_protocol_uses_applescript_and_hides_error_url(self):
        client=BlueBubbles('http://127.0.0.1:1234','private-secret')
        client.request=Mock(return_value={'guid':'sent'})
        client.send('chat','hello','receipt')
        args=client.request.call_args.args
        self.assertEqual(args[0],'message/text');self.assertEqual(args[1]['method'],'apple-script')
        client=BlueBubbles('http://localhost:1234','private-secret');client.opener=Mock()
        client.opener.open.side_effect=RuntimeError('URL with private-secret')
        with self.assertRaises(BridgeError) as caught:client.chat('chat')
        self.assertNotIn('private-secret',str(caught.exception))
