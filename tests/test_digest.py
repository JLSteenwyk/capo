import copy
import json
import tempfile
import time
import unittest
from datetime import datetime,timedelta,timezone,date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch

from capo.digest import DigestStore,DEFAULTS,scope,slot,wall,candidates,calendar_outlook,compose
from capo.digest_feedback import apply
from capo.digest_service import DigestManager,known_thread


class DigestTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.home=Path(self.temp.name)
        self.config=dict(team_id='TTEST',channel_id='CTEST',owner_user_id='UTEST',repositories={})
        self.owner=scope(self.config);self.db=DigestStore(self.home);self.addCleanup(self.db.close)
        self.p=self.db.configure(self.owner,{'enabled':True,'artists':['Example Artist']})
        self.now=datetime(2026,9,14,14,0,tzinfo=timezone.utc)
        self.item=dict(id='news1',number='1',title='New science tool',summary='Scientific software update',
                       topic='scientific software',category='tech',url='https://example.org/tool',
                       published=self.now.isoformat(),source='Official project',why='Useful for your analysis.')
        self.payload=dict(text='Morning digest',news=[self.item])
        self.service=SimpleNamespace(store=SimpleNamespace(home=self.home,list=lambda:[]),config=self.config,
                                     client=Mock(),bot_user_id='UBOT')
        self.service.client.chat_postMessage.return_value={'ok':True,'ts':'123.4'}
        self.manager=DigestManager(self.service);self.addCleanup(self.manager.db.close)

    def run_record(self,status='ready'):
        day,due,deadline=slot(self.now,self.p)
        run=dict(key=self.owner+':'+day,scope=self.owner,day=day,status=status,deadline=deadline.timestamp(),
                 identity=self.config,attempts=0,retry_at=0,created=self.now.timestamp(),payload=self.payload)
        self.db.save(run,self.now);return run

    def test_daily_delivery_is_unique_and_restart_keeps_receipt(self):
        self.run_record()
        self.manager.tick(self.now)
        self.manager.tick(self.now+timedelta(minutes=1))
        restarted=DigestManager(self.service)
        try:restarted.tick(self.now+timedelta(minutes=2))
        finally:restarted.db.close()
        self.service.client.chat_postMessage.assert_called_once()
        self.assertTrue(known_thread(self.home,self.config,'123.4'))
        self.assertFalse(known_thread(self.home,dict(self.config,owner_user_id='UOTHER'),'123.4'))
        self.assertIn('news1',self.db.history(self.owner))

    def test_stale_second_manager_cannot_repeat_a_delivered_run(self):
        run=self.run_record()
        other=DigestManager(self.service)
        try:
            self.manager.deliver(run,self.now)
            other.deliver(run,self.now)
        finally:other.db.close()
        self.service.client.chat_postMessage.assert_called_once()

    def test_expired_daily_run_is_marked_without_posting(self):
        run=self.run_record()
        self.manager.tick(self.now+timedelta(hours=3))
        self.assertEqual(self.db.get(run['key'])['status'],'expired')
        self.service.client.chat_postMessage.assert_not_called()

    def test_uncertain_post_reconciles_without_duplicate(self):
        run=self.run_record();self.service.client.chat_postMessage.side_effect=TimeoutError()
        self.manager.tick(self.now)
        run=self.db.get(run['key'])
        self.assertEqual(run['status'],'sending')
        self.service.client.conversations_history.return_value={'ok':True,'messages':[
            dict(ts='456.7',bot_id='BBOT',user='UBOT',text=self.payload['text'],client_msg_id=run['marker'])]}
        self.manager.tick(self.now+timedelta(minutes=2))
        self.assertEqual(self.db.get(run['key'])['status'],'sent')
        self.service.client.chat_postMessage.assert_called_once()

    def test_formatted_uncertain_post_reconciles_exact_sent_text(self):
        self.payload['text'] = '**Today**\n- Read https://example.org/brief.\n</document>\n</invoke>'
        run = self.run_record()
        self.service.client.chat_postMessage.side_effect = TimeoutError()
        self.manager.tick(self.now)
        run = self.db.get(run['key'])
        wire = self.service.client.chat_postMessage.call_args.kwargs['text']
        self.assertEqual(wire, 'Today\n- Read <https://example.org/brief|example.org>.')
        self.assertTrue(self.service.client.chat_postMessage.call_args.kwargs['mrkdwn'])
        self.assertEqual(run['wire_text'], wire)
        self.service.client.conversations_history.return_value = {'ok':True,'messages':[
            dict(ts='456.7',bot_id='BBOT',user='UBOT',text=wire,client_msg_id=run['marker'])]}
        restarted = DigestManager(self.service)
        try:
            restarted.tick(self.now+timedelta(minutes=2))
        finally:
            restarted.db.close()
        self.assertEqual(self.db.get(run['key'])['status'], 'sent')
        self.service.client.chat_postMessage.assert_called_once()

    def test_failed_history_never_blindly_reposts(self):
        self.run_record();self.service.client.chat_postMessage.side_effect=TimeoutError()
        self.manager.tick(self.now);self.service.client.conversations_history.side_effect=TimeoutError()
        self.manager.tick(self.now+timedelta(minutes=2))
        self.service.client.chat_postMessage.assert_called_once()

    def test_confirmed_absent_post_retries_same_marker(self):
        self.run_record();self.service.client.chat_postMessage.side_effect=[TimeoutError(),{'ok':True,'ts':'456'}]
        self.manager.tick(self.now)
        self.service.client.conversations_history.return_value={'ok':True,'messages':[]}
        self.manager.tick(self.now+timedelta(minutes=2))
        calls=self.service.client.chat_postMessage.call_args_list
        self.assertEqual(len(calls),2)
        self.assertEqual(calls[0].kwargs['client_msg_id'],calls[1].kwargs['client_msg_id'])

    def test_no_late_delivery_or_replay_next_day(self):
        run=self.run_record()
        self.manager.advance(run,self.now+timedelta(hours=3))
        self.assertEqual(self.db.get(run['key'])['status'],'expired')
        self.service.client.chat_postMessage.assert_not_called()

    def test_before_due_and_paused_do_not_generate(self):
        with patch('capo.digest_service.collect') as collect:
            self.manager.tick(self.now-timedelta(minutes=6))
            self.db.configure(self.owner,{'enabled':False})
            self.manager.tick(self.now)
            collect.assert_not_called()
            self.service.client.chat_postMessage.assert_not_called()

    def test_prepared_digest_waits_until_delivery_time(self):
        run=self.run_record();run['not_before']=self.now.timestamp();self.db.save(run,self.now)
        self.manager.advance(run,self.now-timedelta(minutes=1))
        self.service.client.chat_postMessage.assert_not_called()
        self.manager.advance(run,self.now)
        self.service.client.chat_postMessage.assert_called_once()

    def test_dst_uses_local_day_and_handles_gap_and_fold(self):
        a=slot(datetime(2026,3,7,15,tzinfo=timezone.utc),self.p)[1]
        b=slot(datetime(2026,3,8,14,tzinfo=timezone.utc),self.p)[1]
        self.assertEqual(b-a,timedelta(hours=23))
        self.assertEqual(wall(date(2026,3,8),'02:30',self.p['timezone']).hour,3)
        fold=wall(date(2026,11,1),'01:30',self.p['timezone'])
        self.assertEqual(fold.fold,0)

    def test_read_only_generation_recovers_interrupted_worker(self):
        run=self.run_record('building')
        with patch('capo.digest_service.collect',return_value={'news':[]}) as collect, patch('capo.digest_service.compose',return_value=self.payload):
            self.manager.advance(run,self.now)
            worker=self.manager.workers[run['key']];worker.join(3)
            self.assertFalse(worker.is_alive())
            self.assertEqual(self.db.get(run['key'])['status'],'ready')
            collect.assert_called_once()

    def test_feedback_receipt_atomic_and_changes_selection(self):
        other=dict(self.item,id='other',title='AI agent',summary='AI development',topic='AI')
        decision=dict(action='more',item='1',value='',reply='')
        apply(self.db,self.owner,'event1',decision,[self.item])
        apply(self.db,self.owner,'event1',decision,[self.item])
        p=self.db.preferences(self.owner)
        self.assertEqual(p['weights']['scientific software'],2)
        ranked=candidates([other,self.item],p,{})
        self.assertEqual(ranked[0]['id'],'news1')
        decision.update(action='exclude')
        apply(self.db,self.owner,'event2',decision,[self.item])
        self.assertEqual([v['id'] for v in candidates([other,self.item],self.db.preferences(self.owner),{})],['other'])

    def test_feedback_silence_unknown_target_pause_reset_time(self):
        before=self.db.preferences(self.owner)
        apply(self.db,self.owner,'thanks',dict(action='reply',item='',value='',reply='You’re welcome.'),[])
        self.assertEqual(before,self.db.preferences(self.owner))
        response=apply(self.db,self.owner,'bad',dict(action='more',item='9',value='',reply=''),[self.item])
        self.assertIn('Which',response)
        apply(self.db,self.owner,'pause',dict(action='pause',item='',value='',reply=''),[])
        apply(self.db,self.owner,'time',dict(action='time',item='',value='08:30',reply=''),[])
        self.assertFalse(self.db.preferences(self.owner)['enabled'])
        apply(self.db,self.owner,'reset',dict(action='reset',item='',value='',reply=''),[])
        self.assertEqual(self.db.preferences(self.owner)['time'],'07:00')
        self.assertFalse(self.db.preferences(self.owner)['enabled'])

    def test_known_story_not_repeated(self):
        apply(self.db,self.owner,'known',dict(action='known',item='1',value='',reply=''),[self.item])
        self.assertEqual(candidates([self.item],self.p,self.db.history(self.owner)),[])

    def test_calendar_conflicts_gaps_and_upcoming(self):
        def event(title,start,end):return dict(summary=title,start={'dateTime':start},end={'dateTime':end})
        events=[event('First','2026-09-14T10:00:00-07:00','2026-09-14T11:00:00-07:00'),
                event('Second','2026-09-14T10:30:00-07:00','2026-09-14T12:00:00-07:00'),
                event('Next','2026-09-15T09:00:00-07:00','2026-09-15T10:00:00-07:00')]
        today,upcoming,conflicts,gaps=calendar_outlook(events,self.now,self.p)
        self.assertEqual((len(today),len(upcoming),len(conflicts)),(2,1,1))
        self.assertEqual(gaps,['9:00 AM–10:00 AM','12:00 PM–5:00 PM'])

    def test_all_day_busy_blocks_gaps_but_transparent_events_do_not(self):
        event=dict(id='day',summary='Out',start={'date':'2026-09-14'},end={'date':'2026-09-15'})
        self.assertEqual(calendar_outlook([event],self.now,self.p)[3],[])
        event['transparency']='transparent'
        self.assertEqual(calendar_outlook([event],self.now,self.p)[3],['9:00 AM–5:00 PM'])

    def test_preparation_cannot_reference_an_upcoming_event(self):
        event=dict(id='future',summary='Future meeting',start={'dateTime':'2026-09-15T10:00:00-07:00'},end={'dateTime':'2026-09-15T11:00:00-07:00'})
        provider=Mock();provider.call.return_value=dict(news=[],attention=[],preparation='Prepare this today',preparation_event='future',upcoming=['future'])
        evidence=dict(news=[],attention=[],events=[event],now=self.now.isoformat(),coverage=[dict(source='Primary Google Calendar',status='ok')])
        result=compose(evidence,self.p,{},self.home,provider)
        self.assertNotIn('Suggested prep:',result['text'])
        self.assertIn('Future meeting',result['text'])

    def test_model_failure_still_delivers_verified_sections(self):
        provider=Mock();provider.call.side_effect=TimeoutError()
        evidence=dict(news=[],attention=[],events=[],now=self.now.isoformat(),coverage=[dict(source='Primary Google Calendar',status='ok')])
        payload=compose(evidence,self.p,{},self.home,provider)
        self.assertIn('No scheduled events.',payload['text'])
        self.assertIn('News selection',payload['text'])

    def test_composition_limits_categories_and_uses_real_links(self):
        provider=Mock();provider.call.return_value=dict(news=[dict(id='news1',why='Relevant tool.'),dict(id='invented',why='bad')],attention=[],preparation='',preparation_event='',upcoming=[])
        evidence=dict(news=[self.item],attention=[],events=[],now=self.now.isoformat(),coverage=[
            dict(source='Primary Google Calendar',status='ok'),dict(source='BBC World',status='unavailable')])
        result=compose(evidence,self.p,{},self.home,provider)
        self.assertIn(self.item['url'],result['text'])
        self.assertIn('Coverage unavailable: BBC World',result['text'])
        self.assertNotIn('invented',result['text'])
        self.assertEqual(result['news'][0]['number'],'1')
