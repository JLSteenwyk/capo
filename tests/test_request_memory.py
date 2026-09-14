import tempfile
import unittest
from capo.request_memory import RequestMemory


class RequestMemoryTests(unittest.TestCase):
    def test_original_survives_long_thread_restart_and_duplicates(self):
        with tempfile.TemporaryDirectory() as home:
            memory=RequestMemory(home,'owner','thread')
            memory.record('first','owner',{'message':'Find dates and make reminders'})
            memory.record('first','owner',{'message':'Duplicate must not replace original'})
            for i in range(40):memory.record(str(i),'owner',{'message':'Follow-up '+str(i)})
            result=RequestMemory(home,'owner','thread').read()
            self.assertEqual(result['original_request']['message'],'Find dates and make reminders')
            self.assertEqual(len(result['history']),30)
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
