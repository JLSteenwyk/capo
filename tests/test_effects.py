import tempfile
import unittest
from unittest.mock import Mock
from capo.effects import Effects, UncertainEffect


class EffectsTests(unittest.TestCase):
    def test_restart_after_success_and_uncertain_remote_write(self):
        with tempfile.TemporaryDirectory() as home:
            effects=Effects(home,'owner');remote=Mock(return_value={'draft':'synthetic'})
            self.assertEqual(effects.run('event',{'create':'draft'},remote),{'draft':'synthetic'})
            self.assertEqual(Effects(home,'owner').run('event',{'create':'draft'},remote),{'draft':'synthetic'})
            remote.assert_called_once()
            failed=Mock(side_effect=TimeoutError())
            with self.assertRaises(TimeoutError):effects.run('event2',{'update':'draft'},failed)
            with self.assertRaises(UncertainEffect):Effects(home,'owner').run('event2',{'update':'draft'},failed)
            with self.assertRaises(UncertainEffect):effects.run('event3',{'update':'draft'},failed)
            failed.assert_called_once()
            self.assertEqual(effects.run('event2',{'update':'draft'},failed,reconcile=lambda:{'updated':True}),{'updated':True})
            failed.assert_called_once()

    def test_owners_are_isolated_and_receipts_are_bound_to_request(self):
        with tempfile.TemporaryDirectory() as home:
            a=Effects(home,'a');b=Effects(home,'b');write=Mock(return_value={'ok':True})
            a.run('event',{'body':'one'},write)
            with self.assertRaises(ValueError):a.run('event',{'body':'two'},write)
            b.run('event',{'body':'two'},write)
            self.assertEqual(write.call_count,2)

class DraftTransportTests(unittest.TestCase):
    def test_only_draft_endpoints_are_exposed(self):
        from capo.gmail import Gmail
        mail=Gmail.__new__(Gmail)
        mail.drafts_enabled=True
        mail.session=Mock()
        mail.session.request.return_value.ok=True
        mail.session.request.return_value.json.return_value={'id':'synthetic'}
        mail.draft_write('POST',payload={'message':{'raw':'synthetic'}})
        mail.draft_write('PUT','synthetic',{'message':{'raw':'synthetic'}})
        mail.draft_write('DELETE','synthetic')
        for method,id in [('POST','send'),('PUT','../messages/send'),('DELETE',''),('GET','synthetic')]:
            with self.assertRaises(ValueError):mail.draft_write(method,id)
        self.assertEqual(mail.session.request.call_count,3)
        paths=[c.args[1] for c in mail.session.request.call_args_list]
        self.assertTrue(all('/drafts' in path and '/send' not in path for path in paths))
        mail.drafts_enabled=False
        with self.assertRaises(ValueError):mail.draft_write('POST')
        self.assertEqual(mail.session.request.call_count,3)
