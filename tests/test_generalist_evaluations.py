import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch

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

    def test_provider_retry_keeps_written_fixture_and_receipts(self):
        provider=Mock();provider.call.side_effect=[
            step('calendar.events',start='2030-11-04T00:00:00-08:00',end='2030-11-05T00:00:00-08:00'),
            step('calendar.event',calendar_id='primary',event_id='planning'),
            step('calendar.change',action='update',event_id='planning',title='Planning',location='Room 4',
                 start='2030-11-04T14:00:00-08:00',end='2030-11-04T15:00:00-08:00',all_day=False),
            TimeoutError(),finish()]
        now=[100.0]
        def sleep(seconds):now[0]+=seconds
        with tempfile.TemporaryDirectory() as tmp,patch('time.time',side_effect=lambda:now[0]),patch('time.sleep',side_effect=sleep):
            result=run_case(provider,'calendar_move',Path(tmp)/'run',recovery_wait_seconds=180)
        self.assertTrue(result['task_success'])
        self.assertEqual(result['remote_writes'],1)
        self.assertEqual(provider.call.call_count,5)

    def test_injected_email_cannot_pass_with_fabricated_brief(self):
        from capo.evaluations.mail_cases import CASES,MailWorld
        world=MailWorld(CASES['meeting_injection'])
        self.assertIn('<system>',world.message.get_content())
        client=world.mail_client()
        client.get('messages/mail1',{'format':'full'})
        result={'reply':'Room 4, 99 samples and $99000.','receipts':['Discuss samples and the equipment budget.']}
        checks=world.grade(result)
        self.assertFalse(checks['facts_present'])
        self.assertTrue(checks['no_writes'])
        result['reply']='Room 4, 17 samples and $2400.'
        self.assertTrue(all(world.grade(result).values()))

    def test_mail_commitments_create_distinct_task_states(self):
        from test_tasks import fields
        provider=Mock();provider.call.side_effect=[
            step('mail.search',query='subject:Notes',page_size='10',page_token=''),
            step('mail.read',ids=['mail1'],strip_quotes=False),
            step('tasks.save',id='',expected_revision='',fields=fields(title='Send budget report',status='open',sources=['mail1'])),
            step('tasks.save',id='',expected_revision='',fields=fields(title='Consider workshop',status='candidate',sources=['mail1'])),
            step('tasks.save',id='',expected_revision='',fields=fields(title='Get venue address',status='waiting',waiting_on='Morgan',sources=['mail1'])),
            dict(finish(),reply='Tracked the commitment, possible workshop, and reply from Morgan separately.')]
        with tempfile.TemporaryDirectory() as tmp:
            result=run_case(provider,'commitment_notes',Path(tmp)/'run')
        self.assertTrue(result['automated_checks_passed'],result)
        self.assertIsNone(result['task_success'])
        self.assertEqual(result['remote_writes'],0)

    def test_promoting_possible_work_to_commitment_fails(self):
        from capo.evaluations.task_cases import CASES,TaskWorld
        from test_tasks import fields
        world=TaskWorld(CASES['commitment_notes'])
        world.task_rows=[fields(title='Budget report',sources=['mail1']),
            fields(title='Workshop',sources=['mail1']),
            fields(title='Venue',status='waiting',waiting_on='Morgan',sources=['mail1'])]
        checks=world.grade({})
        self.assertFalse(checks['workshop_status'])
        self.assertTrue(checks['budget_status'])

    def test_baseline_missing_github_adapter_is_recorded_without_injection(self):
        from capo.capabilities import shared_tools
        def older_registry(*args,**kwargs):
            registry=shared_tools(*args,**kwargs)
            registry.tools={name:tool for name,tool in registry.tools.items() if not name.startswith('github.')}
            return registry
        provider=Mock();provider.call.return_value=dict(finish(),reply='Current GitHub inspection is unavailable.')
        with tempfile.TemporaryDirectory() as tmp,patch('capo.evaluations.runner.importlib.util.find_spec',return_value=None),patch('capo.capabilities.shared_tools',side_effect=older_registry):
            result=run_case(provider,'github_notification',Path(tmp)/'run')
        self.assertFalse(result['task_success'])
        self.assertFalse(any(name.startswith('github.') for name in result['available_tools']))

    def test_research_date_composes_with_all_day_reminder(self):
        from capo.evaluations.research_cases import URL
        provider=Mock();provider.call.side_effect=[
            step('web.search',query='Lakeside Harbor Hall Oakland November 2030'),
            step('web.read',url=URL),
            step('dates.shift',value='2030-11-18',days='-14',timezone='America/Los_Angeles'),
            step('calendar.events',start='2030-11-04T00:00:00-08:00',end='2030-11-05T00:00:00-08:00'),
            step('calendar.change',action='create',event_id='',title='Decide about Lakeside show',location='',
                start='2030-11-04',end='2030-11-05',all_day=True),
            dict(finish(),reply='Added a November 4 reminder to decide about the November 18 Lakeside show.')]
        with tempfile.TemporaryDirectory() as tmp:
            result=run_case(provider,'research_reminder',Path(tmp)/'run')
        self.assertTrue(result['automated_checks_passed'],result)
        self.assertIsNone(result['task_success'])
        self.assertEqual(result['remote_writes'],1)

    def test_ambiguous_dates_preserve_calendar_and_require_semantic_review(self):
        from capo.evaluations.research_cases import URL
        provider=Mock();provider.call.side_effect=[step('web.search',query='Lakeside Harbor Hall November 2030'),
            step('web.read',url=URL),dict(finish(),reply='There are shows on November 18 and 19. Which one should I use?')]
        with tempfile.TemporaryDirectory() as tmp:
            result=run_case(provider,'research_ambiguous',Path(tmp)/'run')
        self.assertTrue(result['automated_checks_passed'],result)
        self.assertIsNone(result['task_success'])
        self.assertEqual(result['remote_writes'],0)

    def test_empty_search_does_not_supply_a_date(self):
        from capo.evaluations.research_cases import CASES,ResearchWorld
        world=ResearchWorld(CASES['research_no_results'])
        self.assertEqual(world.search('Lakeside')['results'],[])
        self.assertEqual(world.search('Harbor Hall November')['results'],[])
        self.assertTrue(all(world.grade({}).values()))
        world.writes.append(('primary','invented'))
        self.assertFalse(world.grade({})['no_writes'])

    def test_lost_calendar_response_reconciles_without_duplicate_creation(self):
        from capo.evaluations.research_cases import URL
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp)/'run'
            choices=[step('web.read',url=URL),
                step('calendar.change',action='create',event_id='',title='Decide about Lakeside show',location='',
                    start='2030-11-04',end='2030-11-05',all_day=True),step('calendar.pending')]
            def choose(*args):
                if choices:return choices.pop(0)
                receipts=json.loads((output/'request/receipts.json').read_text())
                if receipts[-1]['tool']=='calendar.pending':
                    return step('calendar.reconcile',id=receipts[-1]['result']['actions'][0]['id'])
                return dict(finish(),reply='Confirmed the November 4 reminder; no duplicate created.')
            provider=Mock();provider.call.side_effect=choose
            result=run_case(provider,'research_lost_response',output)
        self.assertTrue(result['automated_checks_passed'],result)
        self.assertEqual(result['remote_writes'],1)
        self.assertTrue(result['checks']['write_reconciled'])

    def test_rate_limited_read_resumes_same_fixture_before_write(self):
        from capo.evaluations.research_cases import URL
        provider=Mock();provider.call.side_effect=[step('web.read',url=URL),step('web.read',url=URL),
            step('calendar.change',action='create',event_id='',title='Decide about Lakeside',location='',
                start='2030-11-04',end='2030-11-05',all_day=True),
            dict(finish(),reply='Added the November 4 reminder after verifying the show date.')]
        with tempfile.TemporaryDirectory() as tmp:
            result=run_case(provider,'research_rate_limit',Path(tmp)/'run',recovery_wait_seconds=5)
        self.assertTrue(result['automated_checks_passed'],result)
        self.assertEqual(result['remote_writes'],1)
        self.assertGreaterEqual(result['recovery_wait_seconds'],1)
        self.assertEqual(provider.call.call_count,4)

    def test_stale_calendar_does_not_write_before_refresh(self):
        from capo.evaluations.calendar_cases import CASES,CalendarWorld
        from capo.calendar_tools import CalendarTools
        from capo.calendar_actions import CalendarActions
        from capo.calendar import CalendarError
        world=CalendarWorld(CASES['calendar_stale'])
        with tempfile.TemporaryDirectory() as tmp:
            tools=CalendarTools('America/Los_Angeles',world.client)
            tools.events('2030-11-04T00:00:00-08:00','2030-11-05T00:00:00-08:00')
            action=CalendarActions(Path(tmp),'owner','America/Los_Angeles',tools.primary_cache)
            with patch('capo.calendar_actions.GoogleCalendar',side_effect=world.client):
                with self.assertRaises(CalendarError):
                    action.change('update','planning','Planning','Room 4','2030-11-04T14:00:00-08:00',
                        '2030-11-04T15:00:00-08:00',False,'attempt')
        self.assertEqual(world.writes,[])
        self.assertEqual(world.stale_changes,1)
        self.assertEqual(world.calendars['primary']['planning']['description'],'Updated preparation notes from another editor.')

    def test_stale_refresh_retries_same_operation_and_preserves_external_notes(self):
        from capo.evaluations.calendar_cases import CASES,CalendarWorld
        from capo.calendar_tools import CalendarTools
        from capo.calendar_actions import CalendarActions
        from capo.calendar import CalendarPreconditionFailed
        world=CalendarWorld(CASES['calendar_stale'])
        with tempfile.TemporaryDirectory() as tmp:
            tools=CalendarTools('America/Los_Angeles',world.client)
            read=lambda:tools.events('2030-11-04T00:00:00-08:00','2030-11-05T00:00:00-08:00')
            read();action=CalendarActions(Path(tmp),'owner','America/Los_Angeles',tools.primary_cache)
            args=('update','planning','Planning','Room 4','2030-11-04T14:00:00-08:00','2030-11-04T15:00:00-08:00',False,'same-operation')
            with patch('capo.calendar_actions.GoogleCalendar',side_effect=world.client):
                with self.assertRaises(CalendarPreconditionFailed):action.change(*args)
                self.assertEqual(action.pending()['actions'],[])
                with self.assertRaises(ValueError):action.change(*args)
                read()
                result=action.change(*args)
                self.assertTrue(result['verified'])
                self.assertEqual(action.change(*args),result)
            db=action.effects.connect()
            try:self.assertEqual(db.execute('SELECT count(*) FROM unsent_attempts').fetchone()[0],1)
            finally:db.close()
        self.assertEqual(len(world.writes),1)
        self.assertTrue(world.grade({'reply':'Moved.'})['remote_state_matches'])

    def test_partial_page_requires_following_full_source(self):
        from capo.evaluations.research_cases import URL
        provider=Mock();provider.call.side_effect=[step('web.read',url=URL),step('web.read',url=URL+'/details'),
            step('calendar.change',action='create',event_id='',title='Decide about Lakeside',location='',
                start='2030-11-04',end='2030-11-05',all_day=True),dict(finish(),reply='Reminder added after reading the full listing.')]
        with tempfile.TemporaryDirectory() as tmp:
            result=run_case(provider,'research_partial',Path(tmp)/'run')
        self.assertTrue(result['automated_checks_passed'],result)
        self.assertTrue(result['checks']['partial_result_encountered'])

    def test_correction_at_proposed_write_prevents_old_time_and_finishes_new_time(self):
        read=step('calendar.events',start='2030-11-04T00:00:00-08:00',end='2030-11-05T00:00:00-08:00')
        change=step('calendar.change',action='update',event_id='planning',title='Planning',location='Room 4',
            start='2030-11-04T14:00:00-08:00',end='2030-11-04T15:00:00-08:00',all_day=False)
        revised=step('calendar.change',action='update',event_id='planning',title='Planning',location='Room 4',
            start='2030-11-04T15:00:00-08:00',end='2030-11-04T16:00:00-08:00',all_day=False)
        provider=Mock();provider.call.side_effect=[read,change,read,revised,dict(finish(),reply='Moved to 3–4 p.m.')]
        with tempfile.TemporaryDirectory() as tmp:
            result=run_case(provider,'calendar_correction',Path(tmp)/'run')
        self.assertTrue(result['automated_checks_passed'],result)
        self.assertEqual(result['correction']['first_status'],'superseded')
        self.assertEqual(result['remote_writes'],1)
