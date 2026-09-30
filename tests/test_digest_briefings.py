import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from capo.digest_briefings import collect,settings


class BriefingTests(unittest.TestCase):
    def test_general_sections_reuse_read_tools_and_deduplicate_seen_findings(self):
        config={'digest_briefings':[{'key':'local-events','title':'Local events','request':'Find matching events.'}]}
        finding=dict(key='example/show',version='2030-03-10',summary='Example show, March 10.',source='https://example.org/show')
        def run(provider,tools,request,directory,**kwargs):
            self.assertEqual(request['interest_context']['interests'][0]['statement'],'I like Example Trio.')
            self.assertIn('memory.search',tools.tools)
            self.assertIn('web.search',tools.tools)
            self.assertTrue(all(not t.mutates for n,t in tools.tools.items() if n!='monitor.report'))
            personalized=dict(finding,interest_key='memory:music.example',relationship='related',why='Related to Example Trio.')
            tools.call('monitor.report',dict(findings=[personalized],blockers=[],coverage='Official event page inspected.'),operation_id='report')
            return {'status':'reported_complete'}
        with tempfile.TemporaryDirectory() as tmp,patch('capo.digest_briefings.research',side_effect=run):
            home=Path(tmp)
            from capo.personal_memory import PersonalMemory
            from capo.capabilities import owner_key
            PersonalMemory(home,owner_key(config),{'text':'I like Example Trio.'}).save('music.example','','I like Example Trio.','owner')
            first=collect(home,config,home/'one',{},provider=object())
            self.assertIn('https://example.org/show',first['text'])
            self.assertIn('You might like:',first['text'])
            seen={f['id']:f for f in first['findings']}
            second=collect(home,config,home/'two',seen,provider=object())
            self.assertEqual(second,{'text':'','findings':[]})

    def test_partial_research_is_not_a_clean_check(self):
        config={'digest_briefings':[{'key':'updates','title':'Updates','request':'Inspect relevant changes.'}]}
        with tempfile.TemporaryDirectory() as tmp,patch('capo.digest_briefings.research',return_value={'status':'partial'}):
            result=collect(Path(tmp),config,Path(tmp)/'run',{},provider=object())
            self.assertEqual(result['findings'],[])
            self.assertIn('could not complete',result['text'])
        with self.assertRaises(ValueError):settings({'digest_briefings':[{'key':'../bad'}]})

    def test_partial_check_reports_what_it_covered_and_rotates_next_time(self):
        config={'digest_briefings':[{'key':'concerts','title':'Music & Bay Area concerts','request':'Find shows.'}]}
        requests=[]
        def run(provider,tools,request,directory,**kwargs):
            requests.append(request)
            tools.call('monitor.report',dict(findings=[],blockers=['Search allowance ran out.'],coverage='Four artist pages.',
                       checked=['Bon Iver','Lucy Dacus','Regina Spektor']),operation_id='report')
            return {'status':'partial'}
        with tempfile.TemporaryDirectory() as tmp,patch('capo.digest_briefings.research',side_effect=run):
            home=Path(tmp)
            first=collect(home,config,home/'one',{},provider=object())
            self.assertEqual(first['text'],'Music & Bay Area concerts\nNothing new for Bon Iver, Lucy Dacus and Regina Spektor. '
                             'The rest weren’t reached today; they’re next in the rotation.')
            self.assertEqual(requests[0]['recently_checked'],[])
            collect(home,config,home/'two',{},provider=object())
            self.assertEqual({row['name'] for row in requests[1]['recently_checked']},{'Bon Iver','Lucy Dacus','Regina Spektor'})

    def test_checked_names_are_optional_and_bounded(self):
        from capo.assignment_reports import AssignmentReport
        with tempfile.TemporaryDirectory() as tmp:
            report=AssignmentReport(Path(tmp))
            report.record(findings=[],blockers=[],coverage='Checked.')
            report.record(findings=[],blockers=[],coverage='Checked.',checked=['One artist'])
            with self.assertRaises(ValueError):
                report.record(findings=[],blockers=[],coverage='Checked.',checked=['x']*31)
