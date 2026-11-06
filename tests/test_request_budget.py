import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from capo.request_budget import pinned,settings
from capo.research_tools import ReadTool,ReadTools,research
from capo.contracts import object_schema


class BudgetTests(unittest.TestCase):
    def test_budget_survives_restart_and_config_change_includes_queue_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);first=pinned(root,received_at=10,now=100)
            self.assertEqual(first['background_at'],130)
            self.assertEqual(first['deadline'],910)
            self.assertEqual(pinned(root,{'total_seconds':1800},now=1000),first)

    def test_bounds_and_finite_total(self):
        for value in ({'total_seconds':0},{'interactive_seconds':200},{'total_seconds':True},{'total_seconds':30}):
            with self.assertRaises(ValueError):settings(value)

    def test_expired_budget_does_not_call_provider_or_mutate(self):
        provider=Mock();write=Mock()
        tools=ReadTools([ReadTool('items.save','Save',object_schema({}),write,True)])
        with tempfile.TemporaryDirectory() as tmp,self.assertRaises(TimeoutError):
            research(provider,tools,{'message':'Save'},Path(tmp),deadline=0)
        provider.call.assert_not_called();write.assert_not_called()

    def test_late_model_decision_cannot_write_after_deadline(self):
        import time
        provider=Mock();provider.call.return_value={'action':'tool','tool':'items.save','arguments_json':'{}','reply':'','document':'','document_title':''}
        write=Mock();tools=ReadTools([ReadTool('items.save','Save',object_schema({}),write,True)])
        def decision(*args,**kwargs):
            time.sleep(.03);return provider.call.return_value
        provider.call.side_effect=decision
        with tempfile.TemporaryDirectory() as tmp,self.assertRaises(TimeoutError):
            research(provider,tools,{'message':'Save'},Path(tmp),deadline=time.time()+.01)
        write.assert_not_called()

    def test_background_notice_and_pinned_deadline_survive_restart(self):
        from capo.capabilities import CapabilityConversation
        from capo.request_timing import RequestTiming
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp);timing=RequestTiming(home,'event')
            timing.mark('received',at=10)
            with patch('capo.request_budget.time.time',return_value=20):
                router=CapabilityConversation(home,{'request_budget':{'interactive_seconds':5,'total_seconds':30}})
                budget=router.budget('event')
            self.assertEqual(budget['deadline'],40)
            with patch('time.time',return_value=30):
                self.assertIn('background',router.progress('event'))
                restarted=CapabilityConversation(home,{'request_budget':{'total_seconds':300}})
                self.assertEqual(restarted.budget('event'),budget)
                restarted.progress('event')
            self.assertEqual(sum(v['phase']=='background' for v in timing.read()),1)
