import json
from contextlib import closing
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from capo.capabilities import Documents, shared_tools
from capo.social_tools import SocialTools, WebError, credential, settings, source_url
from capo.research_tools import ReadTools


def response(text='A public discussion: https://x.com/example/status/123'):
    return {'status':'completed', 'usage':{'input_tokens':10, 'output_tokens':20,
            'server_side_tool_usage_details':{'x_search_calls':1}},
            'output':[{'type':'reasoning','content':[{'type':'output_text','text':'Do not expose reasoning'}]},
                      {'type':'message','content':[{'type':'output_text','text':text}]}]}


class SocialTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.home=Path(self.tmp.name)
        self.tools=SocialTools(self.home,settings({'social_research':{'enabled':True}}))

    def test_registry_is_opt_in_and_has_no_posting_tool(self):
        docs=Documents(self.home,'owner')
        self.assertNotIn('social.search',shared_tools(self.home,{},docs).tools)
        registry=shared_tools(self.home,{'social_research':{'enabled':True}},docs)
        self.assertIn('social.search',registry.tools)
        self.assertFalse(registry.tools['social.search'].mutates)
        self.assertFalse(any(n.startswith('social.') and ('post' in n or 'send' in n) for n in registry.tools))
        with self.assertRaises(ValueError):settings({'social_research':{'enabled':'yes'}})
        with self.assertRaises(ValueError):settings({'social_research':{'daily_requests':21}})

    def test_search_filters_sources_usage_and_reuses_results(self):
        with patch('capo.social_tools.request_search',return_value=response()) as api:
            first=self.tools.search('computational research',['example'],'2026-09-01','2026-09-18')
            self.assertEqual(self.tools.search('computational research',['example'],'2026-09-01','2026-09-18'),first)
            api.assert_called_once()
            payload=api.call_args.args[0]
            self.assertEqual(payload['max_tool_calls'],1)
            self.assertFalse(payload['store'])
            self.assertEqual(payload['tools'][0]['allowed_x_handles'],['example'])
            self.assertEqual(first['sources'],['https://x.com/example/status/123'])
            self.assertNotIn('reasoning',first['summary'])
            self.assertEqual(first['status'],'complete')
            self.assertEqual(first['usage']['input_tokens'],10)

    def test_different_research_tasks_share_same_primitive(self):
        with patch('capo.social_tools.request_search',return_value=response()):
            self.tools.search('recent software release discussion',[],'','')
            self.tools.search('public writing examples',['example'],'','')
            with self.assertRaises(WebError):self.tools.search('third',[],'','')
        registry=ReadTools(self.tools.tools())
        resumed=SocialTools(self.home,settings({}))
        ReadTools(resumed.tools()).restore(registry.snapshot())
        self.assertIn('social.search',ReadTools(resumed.tools()).unavailable())
        self.assertEqual(resumed.next_scan_state(),{})

    def test_daily_limit_persists_across_instances_and_failures(self):
        policy=settings({'social_research':{'daily_requests':1}})
        with patch('capo.social_tools.request_search',side_effect=WebError('API unavailable')) as api:
            with self.assertRaises(WebError):SocialTools(self.home,policy).search('a',[],'','')
            with self.assertRaisesRegex(WebError,'daily'):SocialTools(self.home,policy).search('b',[],'','')
            self.assertEqual(api.call_count,1)
        self.assertEqual(SocialTools(self.home,policy).status()['requests_remaining'],0)
        self.assertTrue(SocialTools(self.home,policy).research_limit('social.search'))
        self.assertFalse(SocialTools(self.home,policy).research_limit('social.status'))
        with closing(sqlite3.connect(self.home/'social-research/usage.sqlite3')) as db:
            self.assertEqual(db.execute('SELECT status FROM requests').fetchone()[0],'failed_or_unconfirmed')

    def test_invalid_inputs_do_not_spend_requests(self):
        with patch('capo.social_tools.request_search') as api:
            for args in [('x',['bad/handle'],'',''),('x',[],'2026-02-30',''),
                         ('x',[],'2026-09-18','2026-09-01'),(' '*2,[],'','')]:
                with self.assertRaises(WebError):self.tools.search(*args)
            api.assert_not_called()
        self.assertFalse((self.home/'social-research/usage.sqlite3').exists())

    def test_no_confirmed_search_or_incomplete_response_is_not_evidence(self):
        for data in [dict(response(),status='incomplete'),dict(response(),usage={})]:
            with patch('capo.social_tools.request_search',return_value=data):
                with self.assertRaisesRegex(WebError,'confirmed X search'):self.tools.search(str(data['usage']),[],'','')

    def test_uncited_summary_has_limited_coverage_and_urls_are_constrained(self):
        with patch('capo.social_tools.request_search',return_value=response('No matching posts found.')):
            result=self.tools.search('rare topic',[],'','')
        self.assertEqual(result['status'],'limited');self.assertEqual(result['sources'],[])
        for url in ['http://x.com/example/status/123','https://x.com@evil.test/example/status/123',
                    'https://x.com.evil.test/example/status/123','https://x.com/example']:
            self.assertFalse(source_url(url))

    def test_credential_requires_private_file_and_does_not_expose_key(self):
        path=self.home/'.config/capo/xai.json';path.parent.mkdir(parents=True)
        path.write_text(json.dumps({'api_key':'synthetic-credential'}));path.chmod(0o644)
        with patch('capo.social_tools.Path.home',return_value=self.home):
            with self.assertRaises(WebError) as raised:credential()
            self.assertNotIn('synthetic-credential',str(raised.exception))
            path.chmod(0o600);self.assertEqual(credential(),'synthetic-credential')

    def test_provider_auth_errors_and_redirects_never_expose_credentials(self):
        from urllib.error import HTTPError
        from capo.social_tools import request_search, NoRedirect
        with patch('capo.social_tools.credential',return_value='private-fixture'), patch('capo.social_tools.build_opener') as opener:
            opener.return_value.open.side_effect=HTTPError('https://api.x.ai/v1/responses',401,'private-fixture',{},None)
            with self.assertRaises(WebError) as caught:request_search({})
            self.assertEqual(str(caught.exception),'Grok API authentication failed.')
            request=opener.return_value.open.call_args.args[0]
            self.assertEqual(request.full_url,'https://api.x.ai/v1/responses')
        self.assertIsNone(NoRedirect().redirect_request(None,None,None,None,None,None))

    def test_parallel_requests_cannot_exceed_daily_allowance(self):
        from concurrent.futures import ThreadPoolExecutor
        def reserve(_):
            try:return SocialTools(self.home,{'daily_requests':1}).reserve()
            except WebError:return None
        with ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(reserve,range(4)))
        self.assertEqual(sum(r is not None for r in results),1)

    def test_empty_dates_bound_search_and_receipt_to_recent_days(self):
        from datetime import datetime, timezone, timedelta
        now=datetime.now(timezone.utc).date()
        with patch('capo.social_tools.request_search',return_value=response()) as api:
            result=self.tools.search('latest research announcements',[],'','')
            spec=api.call_args.args[0]['tools'][0]
            self.assertEqual(spec['from_date'],(now-timedelta(days=3)).isoformat())
            self.assertEqual(spec['to_date'],now.isoformat())
            self.assertEqual(result['search_window'],{'from_date':spec['from_date'],'to_date':spec['to_date']})

    def test_historical_voice_sample_retains_explicit_dates(self):
        with patch('capo.social_tools.request_search',return_value=response()) as api:
            self.tools.search('writing examples',['example'],'2025-01-01','2025-02-01')
            self.assertEqual(api.call_args.args[0]['tools'][0]['from_date'],'2025-01-01')
            self.assertEqual(api.call_args.args[0]['tools'][0]['to_date'],'2025-02-01')
