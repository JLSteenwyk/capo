from json import loads as json_load
import tempfile
import unittest
from capo.request_memory import RequestMemory


class RequestMemoryTests(unittest.TestCase):
    def test_experience_pages_across_threads_without_crossing_owners(self):
        with tempfile.TemporaryDirectory() as home:
            for i in range(11):
                RequestMemory(home,'owner','thread'+str(i%2)).record(str(i),'owner',{'message':str(i)})
            other=RequestMemory(home,'other','thread')
            other.record('private','owner',{'message':'Other owner data'})
            memory=RequestMemory(home,'owner','review')
            first=memory.recent('');second=memory.recent(first['cursor'])
            self.assertEqual(len(first['entries']),8)
            self.assertEqual(len(second['entries']),3)
            self.assertEqual(second['cursor'],'')
            self.assertEqual(len({e['thread'] for e in first['entries']}),2)
            self.assertNotIn('Other owner data',str(first)+str(second))
            self.assertEqual(json_load(memory.evidence(first['entries'][0]['id'])['excerpt'])['message'],'10')
            self.assertEqual(len(other.recent('')['entries']),1)

    def test_experience_limits_large_evidence_and_validates_references(self):
        with tempfile.TemporaryDirectory() as home:
            memory=RequestMemory(home,'owner','thread')
            memory.record('event','receipt',{'body':'x'*20000})
            entry=memory.recent('')['entries'][0]
            self.assertTrue(entry['truncated']);self.assertEqual(len(entry['excerpt']),2000)
            full=memory.evidence(entry['id'])
            self.assertTrue(full['truncated']);self.assertEqual(len(full['excerpt']),12000)
            for bad in ('-1','x','1 OR 1=1','9'*19):
                with self.assertRaises(ValueError):memory.recent(bad)
                with self.assertRaises(ValueError):memory.evidence(bad)
            with self.assertRaises(ValueError):memory.evidence('900')
            self.assertTrue(all(not t.mutates for t in memory.experience_tools()))

    def test_review_document_can_be_recalled_with_its_original_evidence(self):
        from capo.capabilities import Documents, shared_tools, owner_key
        with tempfile.TemporaryDirectory() as home:
            owner=owner_key({})
            RequestMemory(home,owner,'original').record('event','owner',{'message':'Use primary sources'})
            docs=Documents(home,owner)
            registry=shared_tools(home,{},docs)
            entry=registry.tools['experience.history'].execute('')['entries'][0]
            document_id=docs.save('review',{'document_title':'Learning review',
                'document':'Check primary sources. Evidence '+entry['id']+'. Proposed; not tested.'})
            later=shared_tools(home,{},Documents(home,owner))
            review=later.tools['documents.read'].execute(document_id)
            evidence=later.tools['experience.read'].execute(entry['id'])
            self.assertIn('not tested',review['content'])
            self.assertIn('Use primary sources',evidence['excerpt'])
            self.assertEqual(Documents(home,'different-owner').list()['documents'],[])

    def test_original_survives_long_thread_restart_and_duplicates(self):
        with tempfile.TemporaryDirectory() as home:
            memory=RequestMemory(home,'owner','thread')
            memory.record('first','owner',{'message':'Find dates and make reminders'})
            memory.record('notes','notes',{'objective':'Find dates and make reminders','facts':['Source is known'],'uncertainties':['Which performance?'],'next_steps':['Ask which date']})
            memory.record('first','owner',{'message':'Duplicate must not replace original'})
            for i in range(40):memory.record(str(i),'owner',{'message':'Follow-up '+str(i)})
            result=RequestMemory(home,'owner','thread').read()
            self.assertEqual(result['original_request']['message'],'Find dates and make reminders')
            self.assertEqual(len(result['history']),30)
            self.assertEqual(result['latest_working_notes']['uncertainties'],['Which performance?'])
            self.assertIsNone(RequestMemory(home,'someone-else','thread').read()['original_request'])
            self.assertIsNone(RequestMemory(home,'owner','another-thread').read()['original_request'])

    def test_notes_are_not_authority_and_action_receipts_survive(self):
        with tempfile.TemporaryDirectory() as home:
            memory=RequestMemory(home,'owner','thread')
            save=next(t for t in memory.tools('event') if t.name=='context.save').execute
            self.assertFalse(save('Plan travel',['A source suggests Friday'],['Which departure?'],['Verify date'])['verified'])
            memory.record('effect','receipt',{'tool':'tasks.save','result':{'saved':True,'id':'synthetic'}})
            self.assertEqual(memory.read()['history'][-1]['kind'],'receipt')
            with self.assertRaises(ValueError):save('x'*10001,[],[],[])

    def test_actions_remain_pageable_after_long_history(self):
        with tempfile.TemporaryDirectory() as home:
            memory=RequestMemory(home,'owner','thread')
            for i in range(7):memory.record(str(i),'action',{'tool':'tasks.save','result':{'saved':True,'id':str(i)}})
            for i in range(40):memory.record(str(i),'owner',{'message':'Later follow-up'})
            self.assertFalse(any(r['kind']=='action' for r in memory.read()['history']))
            first=memory.actions('');self.assertEqual(len(first['actions']),5)
            second=RequestMemory(home,'owner','thread').actions(first['cursor'])
            self.assertEqual(len(second['actions']),2);self.assertEqual(second['cursor'],'')
            self.assertEqual(RequestMemory(home,'other','thread').actions('')['actions'],[])

    def test_older_source_receipts_remain_retrievable(self):
        with tempfile.TemporaryDirectory() as home:
            memory=RequestMemory(home,'owner','thread')
            memory.record('source','receipt',{'tool':'web.read','result':{'url':'https://example.com/official','text':'Verified date'}})
            for i in range(40):memory.record(str(i),'owner',{'message':'Later clarification'})
            self.assertFalse(any(r['kind']=='receipt' for r in memory.read()['history']))
            history=RequestMemory(home,'owner','thread').history('')
            self.assertEqual(history['entries'][0]['data']['result']['url'],'https://example.com/official')
            self.assertTrue(history['cursor'])
            self.assertEqual(RequestMemory(home,'other','thread').history('')['entries'],[])
