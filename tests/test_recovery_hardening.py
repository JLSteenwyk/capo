import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from capo.monitoring import Monitor
from capo.social_tools import SocialTools, SocialTransient, WebError
from capo.research_tools import ReadTools
from capo.delivery_recovery import defer
from capo.update_runtime import idle_reason
from capo.digest import DigestStore


class RecoveryHardeningTests(unittest.TestCase):
    def test_parked_delivery_is_not_active_work_and_retains_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp);now=datetime.now(timezone.utc)
            db=DigestStore(home)
            run=dict(key='one',scope='owner',day='2030-01-01',status='sending',
                     created=now.timestamp()-1800,marker='original',wire_text='original payload')
            db.save(run,now)
            self.assertEqual(idle_reason(home),'scheduled work')
            defer(run,now.timestamp());db.save(run,now)
            self.assertEqual(run['status'],'delivery_unknown')
            self.assertEqual(idle_reason(home),'')
            self.assertEqual(run['wire_text'],'original payload')
            self.assertEqual(run['marker'],'original')
            db.close()

    def test_retry_without_source_reappearing_refreshes_and_clears_only_covered_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            m=Monitor(Path(tmp),{})
            now=datetime.now(timezone.utc)
            item={'kind':'email','message_id':'abc123','id':'abc123','subject':'Review proposal','snippet':'Please review.'}
            def initial(*args,**kwargs):return {'status':'partial','reply':'unverified'}
            with patch('capo.monitoring.research',side_effect=initial):m.tick(now,[item])
            self.assertEqual(len(m.notices()),1)
            refreshed={'status':'checked','messages':[{'sent_by_owner':True,'snippet':'Review complete.'}]}
            def completed(provider,tools,request,*args,**kwargs):
                batch=tools.call('observations.read',{})['observations']
                self.assertEqual(batch[0]['thread_evidence'],refreshed)
                return {'status':'reported_complete','reply':'Reviewed.'}
            # Restart, empty incoming scan: due failure still gets inspected.
            m=Monitor(Path(tmp),{})
            with patch('capo.task_evidence.TaskEvidence.source',return_value=refreshed) as refresh,patch('capo.monitoring.research',side_effect=completed) as review:
                m.tick(now+timedelta(hours=2),[])
                refresh.assert_called_once();review.assert_called_once()
            self.assertEqual(m.notices(),[])
            self.assertEqual(m.observations.unresolved(['email:abc123']),[])

    def test_failed_refresh_is_bounded_and_not_silently_resolved(self):
        with tempfile.TemporaryDirectory() as tmp:
            m=Monitor(Path(tmp),{});now=datetime.now(timezone.utc)
            item={'kind':'email','message_id':'abc123','id':'abc123'}
            with patch('capo.monitoring.research',return_value={'status':'partial'}):m.tick(now,[item])
            with patch('capo.task_evidence.TaskEvidence.source',side_effect=TimeoutError()) as refresh,patch('capo.monitoring.research') as review:
                m.tick(now+timedelta(hours=2),[]);m.tick(now+timedelta(hours=4),[])
                refresh.assert_called_once();review.assert_not_called()
            self.assertEqual(len(m.notices()),1)
            self.assertTrue(m.observations.unresolved(['email:abc123']))

    def test_social_transient_retry_spends_existing_limits(self):
        with tempfile.TemporaryDirectory() as tmp:
            tools=SocialTools(Path(tmp),{'daily_requests':5})
            result={'status':'completed','usage':{'server_side_tool_usage_details':{'x_search_calls':1}},
                    'output':[{'type':'message','content':[{'type':'output_text','text':'https://x.com/example/status/123'}]}]}
            with patch('capo.social_tools.request_search',side_effect=[SocialTransient('temporary'),result]) as api:
                self.assertEqual(tools.search('recent research',[],'','')['status'],'complete')
                self.assertEqual(api.call_count,2)
            self.assertEqual(tools.status()['requests_used'],2)
            with self.assertRaises(WebError):tools.search('another search',[],'','')
            resumed=SocialTools(Path(tmp),{'daily_requests':5})
            ReadTools(resumed.tools()).restore(ReadTools(tools.tools()).snapshot())
            self.assertIn('social.search',ReadTools(resumed.tools()).unavailable())

    def test_social_retry_does_not_extend_time_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            tools=SocialTools(Path(tmp),{'daily_requests':5})
            with patch('capo.social_tools.time.monotonic',side_effect=[0,0,91]), \
                 patch('capo.social_tools.request_search',side_effect=SocialTransient('temporary')) as api:
                with self.assertRaisesRegex(SocialTransient,'time budget'):tools.search('recent research',[],'','')
                api.assert_called_once()
                self.assertEqual(api.call_args.kwargs['timeout'],90)
            self.assertEqual(tools.status()['requests_used'],1)

    def test_social_daily_limit_and_permanent_errors_do_not_retry(self):
        for limit,error in [(1,SocialTransient('temporary')),(5,WebError('authentication failed'))]:
            with self.subTest(limit=limit),tempfile.TemporaryDirectory() as tmp:
                tools=SocialTools(Path(tmp),{'daily_requests':limit})
                with patch('capo.social_tools.request_search',side_effect=error) as api:
                    with self.assertRaises(WebError):tools.search('recent research',[],'','')
                    api.assert_called_once()
                self.assertEqual(tools.status()['requests_used'],1)
