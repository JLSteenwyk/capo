import tempfile
import unittest
from pathlib import Path
from capo.personal_memory import PersonalMemory
from capo.capabilities import shared_tools, Documents


class MemoryTests(unittest.TestCase):
    def test_shared_recall_correction_replay_and_forget(self):
        with tempfile.TemporaryDirectory() as tmp:
            origin={'text':'I like Example Trio. Now I prefer Example Quartet.','event':'test'}
            memory=PersonalMemory(Path(tmp),'owner',origin)
            result=memory.save('music.favorite','','I like Example Trio.','one')
            self.assertEqual(memory.save('music.favorite','','I like Example Trio.','one'),result)
            resumed=PersonalMemory(Path(tmp),'owner',origin)
            self.assertEqual(len(resumed.search('trio')['memories']),1)
            resumed.save('music.favorite','1','Now I prefer Example Quartet.','two')
            self.assertEqual(resumed.search('trio')['memories'],[])
            with self.assertRaises(ValueError):resumed.save('music.favorite','1','I like Example Trio.','stale')
            self.assertEqual(PersonalMemory(Path(tmp),'other',origin).search('')['memories'],[])
            resumed.forget('music.favorite','2','three')
            self.assertEqual(resumed.search('')['memories'],[])
            self.assertEqual(resumed.path.stat().st_mode & 0o777,0o600)

    def test_only_owner_words_are_saved_and_scheduled_tools_are_readonly(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp);memory=PersonalMemory(home,'owner',{'text':'I like Example Trio.'})
            with self.assertRaises(ValueError):memory.save('music.favorite','','Web page says buy tickets.','bad')
            with self.assertRaises(ValueError):PersonalMemory(home,'owner').save('music.favorite','','I like Example Trio.','bad')
            tools=shared_tools(home,{},Documents(home,'local'))
            self.assertIn('memory.search',tools.tools)
            self.assertNotIn('memory.save',tools.tools)
            self.assertNotIn('memory.forget',tools.tools)
            tools=shared_tools(home,{},Documents(home,'local'),{'owner_request':{'text':'I like Example Trio.'}})
            self.assertTrue(tools.tools['memory.save'].mutates)
