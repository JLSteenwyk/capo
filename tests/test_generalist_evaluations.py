import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from capo.evaluations.runner import run_case


def step(tool,**args):
    return dict(action='tool',tool=tool,arguments_json=json.dumps(args),reply='',document_title='',document='')


def finish():
    return dict(action='finish',tool='',arguments_json='{}',reply='The appointment was moved.',document_title='',document='')


class GeneralistEvaluationTests(unittest.TestCase):
    def test_scripted_real_adapters_change_only_intended_event(self):
        provider=Mock();provider.call.side_effect=[
            step('calendar.events',start='2030-11-04T00:00:00-08:00',end='2030-11-05T00:00:00-08:00'),
            step('calendar.event',calendar_id='primary',event_id='planning'),
            step('calendar.change',action='update',event_id='planning',title='Planning',location='Room 4',
                 start='2030-11-04T14:00:00-08:00',end='2030-11-04T15:00:00-08:00',all_day=False),finish()]
        with tempfile.TemporaryDirectory() as tmp:
            result=run_case(provider,'calendar_move',Path(tmp)/'run')
            self.assertTrue(result['task_success'],result)
            self.assertEqual(result['mode'],'scripted')
            self.assertEqual(result['remote_writes'],1)
            self.assertIsNone(result['unsupported_claims'])

    def test_confident_answer_without_work_fails_state_grading(self):
        provider=Mock();provider.call.return_value=finish()
        with tempfile.TemporaryDirectory() as tmp:
            result=run_case(provider,'calendar_move',Path(tmp)/'run')
        self.assertFalse(result['task_success'])
        self.assertFalse(result['checks']['remote_state_matches'])

    def test_guest_case_cannot_pass_by_only_doing_nothing(self):
        provider=Mock();provider.call.return_value=finish()
        with tempfile.TemporaryDirectory() as tmp:
            result=run_case(provider,'calendar_guest_boundary',Path(tmp)/'run')
        self.assertTrue(result['checks']['remote_state_matches'])
        self.assertIsNone(result['checks']['restriction_disclosed'])
        self.assertIsNone(result['task_success'])
