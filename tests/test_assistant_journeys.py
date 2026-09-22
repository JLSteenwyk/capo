"""Scripted owner-message journeys through real routing, adapters and Slack delivery.

Only provider decisions and external services are synthetic. State assertions,
not a provider's 'done' message, determine success. No live account is contacted.
"""
import base64
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock,patch

from capo.slack import SlackService,ingest
from capo.store import Store
from restart_fixture import Remote,PLAN


def tool(name,args):
    return dict(action='tool',tool=name,arguments_json=json.dumps(args),reply='',document_title='',document='')


def finish(outcomes,reply,document='',title=''):
    return dict(action='finish',tool='',arguments_json=json.dumps({'outcomes':outcomes}),reply=reply,document_title=title,document=document)


class AssistantJourneyTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        self.config={'team_id':'T123','channel_id':'C123','owner_user_id':'U123','auto_run':False,'repositories':{'fixture':{'path':str(self.root),'checks':['python3 -V']}},
                     'calendar':{'enabled':True,'timezone':'America/Los_Angeles'}}
        self.store=Store(self.root/'capo');self.addCleanup(self.store.db.close)
        self.remote=Remote(self.root);self.client=Mock();self.client.chat_postMessage.side_effect=self.remote.chat_postMessage
        self.service=SlackService(self.store,self.config,self.client);self.service.bot_user_id='synthetic-bot'
        self.legacy=patch('capo.slack.legacy_conversation',return_value=False);self.legacy.start();self.addCleanup(self.legacy.stop)

    def body(self,text,files=None):
        event={'type':'app_mention','channel':'C123','user':'U123','text':'<@UBOT> '+text,'ts':'1.000'}
        if files:event['files']=files
        return {'team_id':'T123','event_id':'journey','event':event}

    def deliver(self,body):
        self.assertTrue(ingest(self.store.home,self.config,body))
        end=time.monotonic()+8
        while self.store.pending_slack() and time.monotonic()<end:
            self.service.process_messages()
            # Advance the local delivery throttle only; actual work stays on real clocks.
            with self.store.db:self.store.db.execute('DELETE FROM slack_delivery_clock')
            time.sleep(.005)
        self.assertFalse(self.store.pending_slack(),'Synthetic journey did not finish')
        return ''.join(m['text'] for m in self.remote.state()['messages'])

    def test_screenshot_to_three_calendar_events_delivers_every_result_and_preserves_existing(self):
        from capo.calendar import event_body
        from capo.conversation import _write
        state=self.remote.state();state['events']['existing']=dict(event_body(dict(PLAN,title='Keep this event'),'America/Los_Angeles'),id='existing')
        _write(self.remote.path,state)
        before=state['events']['existing']
        names=['Breakfast','Opening talk','Lunch']
        plan=[dict(id=str(i),requirement=name,kind='action') for i,name in enumerate(names)]
        outcomes=[dict(item_id=str(i),requirement=name,kind='action',status='complete',evidence=[str(i+2)],next_step='') for i,name in enumerate(names)]
        provider=Mock();provider.call.side_effect=[
            tool('calendar.events',{'start':'2030-11-04T00:00:00-08:00','end':'2030-11-05T00:00:00-08:00'}),
            tool('work.track',{'items':plan,'source_receipts':['0']}),
            *[tool('calendar.change',dict(PLAN,title=name)) for name in names],
            finish(outcomes,'Added all three events.','\n'.join('- '+name for name in names),'Workshop schedule')]
        image=Mock();image.call.return_value={'observations':'November 4, 2030: Breakfast, Opening talk, Lunch. Each is an all-day reminder.','uncertainties':''}
        with patch('capo.capabilities.Providers',return_value=provider),patch('capo.slack_images.Providers',return_value=image),\
             patch('capo.slack_images.download',return_value={'media_type':'image/png','data':base64.b64encode(b'synthetic image').decode()}),\
             patch('capo.calendar.GoogleCalendar',return_value=self.remote),patch('capo.calendar_actions.GoogleCalendar',return_value=self.remote):
            delivered=self.deliver(self.body('Keep my existing event and add each of the three reminders in this image.',[{'id':'F123','mimetype':'image/png'}]))
        saved=self.remote.state()['events']
        self.assertEqual(len(saved),4);self.assertEqual(saved['existing'],before)
        for name in names:self.assertEqual(sum(e['summary']==name for e in saved.values()),1);self.assertIn(name,delivered)
        self.assertEqual(image.call.call_count,1)
        self.assertIn('Opening talk',provider.call.call_args.args[1])
        self.assertNotIn('</invoke>',delivered)
        # Duplicate delivery of the same Slack event does not repeat reasoning or writes.
        calls=provider.call.call_count;state_before=self.remote.state()
        ingest(self.store.home,self.config,self.body('duplicate'))
        self.service.process_messages();self.assertEqual(provider.call.call_count,calls)
        self.assertEqual(self.remote.state(),state_before)

    def test_document_contents_and_clickable_links_survive_full_slack_delivery(self):
        provider=Mock();provider.call.return_value=finish(
            [dict(requirement='Provide the complete reference list',kind='answer',status='complete',evidence=[],next_step='')],
            'Here is the list.',document='**Ingredients**\\n\\n- 400 g tofu\\n- 2 tbsp sauce\\n[Source](https://example.invalid/recipe)\n</document>\n</invoke>',title='Shopping list')
        with patch('capo.capabilities.Providers',return_value=provider):
            delivered=self.deliver(self.body('Format this supplied list: 400 g tofu, 2 tbsp sauce. Source https://example.invalid/recipe'))
        self.assertIn('400 g tofu',delivered);self.assertIn('2 tbsp sauce',delivered)
        self.assertIn('<https://example.invalid/recipe|example.invalid>',delivered)
        self.assertNotIn('\\n',delivered);self.assertNotIn('</invoke>',delivered)

    def test_lost_slack_acknowledgement_survives_service_restart_without_duplicate(self):
        body=self.body('Give me a short answer.')
        self.assertTrue(ingest(self.store.home,self.config,body))
        def accepted_but_lost(**payload):
            self.remote.chat_postMessage(**payload)
            raise TimeoutError()
        self.client.chat_postMessage.side_effect=accepted_but_lost
        with patch.object(self.service,'dispatch',return_value='The complete answer.'):
            self.service.process_messages()
        self.assertTrue(self.store.pending_slack())
        self.assertEqual(len(self.remote.state()['messages']),1)
        restarted=SlackService(self.store,self.config,self.client);restarted.bot_user_id='synthetic-bot'
        self.client.conversations_replies.side_effect=self.remote.conversations_replies
        with patch('capo.slack.time.time',return_value=time.time()+90),patch.object(restarted,'dispatch',side_effect=AssertionError('Do not regenerate')):
            restarted.process_messages()
        self.assertFalse(self.store.pending_slack())
        self.assertEqual(len(self.remote.state()['messages']),1)
        self.assertEqual(self.client.chat_postMessage.call_count,1)
