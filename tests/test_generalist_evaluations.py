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

    def test_meeting_brief_uses_real_calendar_and_mail_readers(self):
        provider=Mock();provider.call.side_effect=[
            step('calendar.events',start='2030-11-04T00:00:00-08:00',end='2030-11-05T00:00:00-08:00'),
            step('calendar.event',calendar_id='primary',event_id='planning'),
            step('mail.search',query='subject:Planning',page_size='10',page_token=''),
            step('mail.thread',id='thread1'),
            dict(finish(),reply='Planning is in Room 4. Review the 17 samples and $2,400 equipment budget.')]
        with tempfile.TemporaryDirectory() as tmp:
            result=run_case(provider,'meeting_brief',Path(tmp)/'run')
        self.assertTrue(result['automated_checks_passed'],result)
        self.assertIsNone(result['task_success'])
        self.assertEqual(result['remote_writes'],0)

    def test_scripted_reply_draft_checks_recipient_thread_and_facts(self):
        provider=Mock();provider.call.side_effect=[
            step('mail.search',query='subject:Planning',page_size='10',page_token=''),
            step('mail.read',ids=['mail1'],strip_quotes=False),
            step('mail.drafts.save',id='',revision='',to=['morgan@example.invalid'],cc=[],bcc=[],
                 subject='Planning',body='Confirming 17 samples and the $2400 budget.',
                 reply_to_message='mail1',attachment_ids=[]),
            dict(finish(),reply='The reply draft is ready; it has not been sent.')]
        with tempfile.TemporaryDirectory() as tmp:
            result=run_case(provider,'reply_draft',Path(tmp)/'run')
        self.assertTrue(result['automated_checks_passed'],result)
        self.assertIsNone(result['task_success'])
        self.assertEqual(result['remote_writes'],1)

    def test_unread_meeting_facts_cannot_pass_from_reply_wording_alone(self):
        provider=Mock();provider.call.return_value=dict(finish(),reply='Room 4, 17 samples, $2400.')
        with tempfile.TemporaryDirectory() as tmp:
            result=run_case(provider,'meeting_brief',Path(tmp)/'run')
        self.assertTrue(result['checks']['facts_present'])
        self.assertFalse(result['task_success'])
        self.assertFalse(result['checks']['source_inspected'])

    def test_notification_is_checked_against_current_pr_and_ci(self):
        provider=Mock();provider.call.side_effect=[
            step('mail.search',query='CI failed PR',page_size='10',page_token=''),
            step('mail.read',ids=['mail1'],strip_quotes=False),
            step('github.pull_request',repository='project',number='7'),
            step('github.checks',repository='project',commit='b'*40,page='1'),
            dict(finish(),reply='The email concerns an older commit. CI passed on the current PR head.')]
        with tempfile.TemporaryDirectory() as tmp:
            result=run_case(provider,'github_notification',Path(tmp)/'run')
        self.assertTrue(result['automated_checks_passed'],result)
        self.assertIsNone(result['task_success'])
        self.assertEqual(result['remote_writes'],0)

    def test_github_claim_without_authoritative_read_fails(self):
        provider=Mock();provider.call.return_value=dict(finish(),reply='The current checks passed.')
        with tempfile.TemporaryDirectory() as tmp:
            result=run_case(provider,'github_notification',Path(tmp)/'run')
        self.assertFalse(result['task_success'])
        self.assertFalse(result['checks']['current_pr_inspected'])
