import tempfile
import unittest
from pathlib import Path
from capo.personalization import snapshot, finding_text
from capo.personal_memory import PersonalMemory
from capo.capabilities import owner_key
from capo.assignment_reports import AssignmentReport,finish


class PersonalizationTests(unittest.TestCase):
    def test_fresh_runs_follow_corrections_but_retries_keep_their_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp);owner=owner_key({})
            m=PersonalMemory(home,owner,{'text':'I like jazz. I prefer folk now.'})
            m.save('music.favorite','','I like jazz.','one')
            first=snapshot(home,{},home/'first')
            m.save('music.favorite','1','I prefer folk now.','two')
            self.assertEqual(snapshot(home,{},home/'first'),first)
            second=snapshot(home,{},home/'second')
            self.assertEqual(second['interests'][0]['statement'],'I prefer folk now.')
            with self.assertRaises(ValueError):snapshot(home,{'owner_user_id':'OTHER'},home/'first')
            m.forget('music.favorite','2','three')
            self.assertEqual(snapshot(home,{},home/'third')['interests'],[])

    def test_related_discoveries_are_grounded_labeled_and_do_not_become_preferences(self):
        for topic,statement,summary in [('music','I like jazz.','A related musician is playing.'),
                                        ('style','I prefer linen.','A breathable cotton option is available.')]:
            with self.subTest(topic=topic),tempfile.TemporaryDirectory() as tmp:
                home=Path(tmp);m=PersonalMemory(home,owner_key({}),{'text':statement})
                m.save(topic+'.preference','',statement,'owner')
                context=snapshot(home,{},home/'run')
                report=AssignmentReport(home/'run',context['interests'])
                finding=dict(key='example/item',version='v1',summary=summary,source='https://example.org/item',
                             interest_key='memory:'+topic+'.preference',relationship='related',why='A possible match to your stated preference.')
                report.record(findings=[finding],blockers=[],coverage='Official source inspected.')
                run={'title':'Updates'};finish(run,report.read(),{})
                self.assertIn('You might like:',run['payload']['text'])
                self.assertIn(finding['why'],run['payload']['text'])
                self.assertEqual(len(m.search('')['memories']),1)
                finding['interest_key']='invented'
                with self.assertRaises(ValueError):report.record(findings=[finding],blockers=[],coverage='Inspected.')

    def test_irrelevant_preferences_do_not_require_a_fake_connection(self):
        with tempfile.TemporaryDirectory() as tmp:
            report=AssignmentReport(Path(tmp),[{'key':'music:example'}])
            finding=dict(key='deadline',version='v1',summary='A deadline is approaching.',source='https://example.org/deadline',
                         relationship='none',interest_key='',why='')
            report.record(findings=[finding],blockers=[],coverage='Inspected.')
            self.assertEqual(finding_text(finding),finding['summary'])
