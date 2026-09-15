import tempfile
import unittest
from pathlib import Path

from capo.digest import DigestStore, scope
from capo.digest_tools import DigestTools


class DigestToolTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.home=Path(tmp.name)
        self.config={'team_id':'T123','channel_id':'C123','owner_user_id':'U123'}
        self.request={'request_thread':'thread'}
        self.tools=DigestTools(self.home,self.config,self.request)
        self.db=DigestStore(self.home); self.addCleanup(self.db.close)
        self.db.configure(scope(self.config), {'enabled':True, 'time':'07:00'})

    def test_multiple_changes_compose_and_replay_after_restart(self):
        first=self.tools.read()
        self.tools.change('more','','AI',first['revision'],'one')
        second=self.tools.read()
        self.tools.change('artist','','Example Artist',second['revision'],'two')
        restarted=DigestTools(self.home,self.config,self.request)
        restarted.change('more','','AI',first['revision'],'one')
        current=restarted.read()
        self.assertEqual(current['preferences']['weights']['AI'],2)
        self.assertIn('Example Artist',current['preferences']['artists'])
        self.assertEqual(len(current['recent_changes']),2)
        self.assertEqual(current['preferences']['time'],'07:00')
        self.assertTrue(current['preferences']['enabled'])

    def test_stale_revision_and_changed_receipt_do_not_overwrite_settings(self):
        before=self.tools.read()
        self.db.configure(scope(self.config), {'time':'08:00'})
        with self.assertRaises(ValueError):
            self.tools.change('pause','','',before['revision'],'stale')
        current=self.tools.read()
        self.tools.change('more','','AI',current['revision'],'one')
        with self.assertRaises(ValueError):
            self.tools.change('less','','AI',current['revision'],'one')
        result=self.tools.read()['preferences']
        self.assertEqual(result['time'],'08:00')
        self.assertTrue(result['enabled'])
        self.assertEqual(result['weights']['AI'],2)

    def test_unknown_items_and_uninspected_preferences_cannot_change(self):
        with self.assertRaises(ValueError):
            self.tools.change('pause','','','unknown','one')
        current=self.tools.read()
        with self.assertRaises(ValueError):
            self.tools.change('known','5','',current['revision'],'two')
        self.assertEqual(self.tools.read()['recent_changes'],[])
        other=DigestTools(self.home,dict(self.config,owner_user_id='UOTHER'),self.request)
        self.assertEqual(other.read()['recent_changes'],[])


if __name__=='__main__':unittest.main()
