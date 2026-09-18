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
            self.assertIn('memory.search',tools.tools)
            self.assertIn('web.search',tools.tools)
            self.assertTrue(all(not t.mutates for n,t in tools.tools.items() if n!='monitor.report'))
            tools.call('monitor.report',dict(findings=[finding],blockers=[],coverage='Official event page inspected.'),operation_id='report')
            return {'status':'reported_complete'}
        with tempfile.TemporaryDirectory() as tmp,patch('capo.digest_briefings.research',side_effect=run):
            home=Path(tmp)
            first=collect(home,config,home/'one',{},provider=object())
            self.assertIn('https://example.org/show',first['text'])
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
