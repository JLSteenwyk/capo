import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from capo.assignment_reports import finish, research_context
from capo.capabilities import owner_key
from capo.check_recovery import queue_followups
from capo.digest import DigestStore, compose, scope
from capo.digest_briefings import collect
from capo.digest_service import DigestManager
from capo.health import Health
from capo.message_format import plain_text, slack_text
from capo.schedules import Schedules
from capo.task_evidence import TaskEvidence
from capo.team_status import TeamStatus

NOW = datetime(2030, 1, 3, 20, tzinfo=timezone.utc)
SCHEDULE = {'revision': 1, 'enabled': True, 'timezone': 'UTC', 'weekdays': list('0123456')}


def finding(key, version, **extra):
    return dict(key=key, version=version, summary='Summary of '+key+'.', source='https://example.org/'+key, **extra)


class FollowupPolicyTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.db = DigestStore(Path(tmp.name)); self.addCleanup(self.db.close)

    def original(self, sid, **fields):
        run = dict(dict(key=sid+':run', scope='owner', day='2030-01-03', status='sent', ts='100.1',
                   created=NOW.timestamp()-3600, deadline=NOW.timestamp()-1800, schedule_id=sid, revision=1,
                   request='Inspect sources', title='Check '+sid, identity={}), **fields)
        self.db.save(run, NOW-timedelta(minutes=30))
        return run

    def queued(self, sid):
        return self.db.get(sid+':run:followup')

    def test_delivered_partial_rotation_and_next_run_blockers_queue_nothing(self):
        # A change-only monitor rotating repositories, and a drafting request whose
        # search allowance ran out. Both were delivered; the next run handles gaps.
        self.original('monitor', outcome_status='partial',
                      report={'findings': [], 'blockers': ['Next run: recheck the remaining repositories.'], 'coverage': 'Batch 1.'})
        self.original('drafts', outcome_status='partial')
        self.original('plan', outcome_status='reported_complete',
                      report={'findings': [], 'blockers': ['Recheck two items in a later run.'], 'coverage': 'Tasks.'})
        schedules = {sid: dict(SCHEDULE, id=sid) for sid in ('monitor', 'drafts', 'plan')}
        queue_followups(self.db, 'owner', schedules, NOW)
        self.assertEqual([self.queued(sid) for sid in schedules], [None, None, None])

    def test_failed_run_queues_exactly_one_top_level_followup(self):
        for sid in ('monitor', 'drafts'):
            self.original(sid, status='failed', ts=None)
        schedules = {sid: dict(SCHEDULE, id=sid) for sid in ('monitor', 'drafts')}
        queue_followups(self.db, 'owner', schedules, NOW)
        queue_followups(self.db, 'owner', schedules, NOW+timedelta(minutes=1))
        for sid in schedules:
            child = self.queued(sid)
            self.assertEqual(child['recovery_of'], sid+':run')
            self.assertNotIn('thread_ts', child)
        self.assertEqual(self.db.db.execute("SELECT COUNT(*) FROM runs WHERE key LIKE '%:followup'").fetchone()[0], 2)

    def test_unfinished_verification_after_delivery_replies_in_thread(self):
        self.original('monitor', verification_incomplete=True)
        queue_followups(self.db, 'owner', {'monitor': dict(SCHEDULE, id='monitor')}, NOW)
        self.assertEqual(self.queued('monitor')['thread_ts'], '100.1')


class FollowupDeliveryTests(unittest.TestCase):
    previous = {'findings': [finding('repo/ci', 'failing at abc')], 'blockers': ['Batch 2 unchecked.'], 'coverage': 'Batch 1.'}

    def followup(self, **fields):
        return dict(key='k:followup', title='GitHub watch', created=NOW.timestamp(), recovery_of='k', thread_ts='100.1', **fields)

    def test_followup_without_new_findings_posts_nothing(self):
        run = self.followup()
        finish(run, {'findings': [finding('repo/ci', 'failing at abc')],
                     'blockers': ['Rephrased: batch 2 still unchecked.'], 'coverage': 'Batch 1 again.'}, self.previous)
        self.assertEqual(run['status'], 'quiet')

    def test_followup_posts_only_new_findings_as_thread_reply(self):
        run = self.followup()
        finish(run, {'findings': [finding('repo/ci', 'failing at abc'), finding('repo/alert', '#70')],
                     'blockers': ['Rephrased: batch 2 still unchecked.'], 'coverage': 'Batch 2.'}, self.previous)
        text = run['payload']['text']
        self.assertEqual(run['status'], 'ready')
        self.assertIn('repo/alert', text); self.assertNotIn('repo/ci', text); self.assertNotIn('Needs attention', text)
        run.update(key='owner:scheduled:k:followup', scope='owner', day='2030-01-03', identity={'channel_id': 'CTEST'},
                   deadline=NOW.timestamp()+900)
        with tempfile.TemporaryDirectory() as tmp:
            client = Mock(); client.chat_postMessage.return_value = {'ok': True, 'ts': '200.2'}
            config = {'team_id': 'T1', 'channel_id': 'CTEST', 'owner_user_id': 'U1'}
            service = SimpleNamespace(store=SimpleNamespace(home=Path(tmp)), config=config, client=client, bot_user_id='UBOT')
            manager = DigestManager(service); self.addCleanup(manager.db.close)
            manager.db.save(run, NOW); manager.deliver(run, NOW)
            self.assertEqual(client.chat_postMessage.call_args.kwargs['thread_ts'], '100.1')
            # An uncertain thread post reconciles against the thread, not channel history.
            uncertain = dict(manager.db.get(run['key']), status='delivery_unknown', retry_at=0)
            client.conversations_replies.return_value = {'ok': True, 'messages': [
                {'bot_id': 'B', 'user': 'UBOT', 'ts': '200.2', 'text': uncertain['wire_text'],
                 'metadata': {'event_payload': {'key': uncertain['marker']}}}]}
            self.assertEqual(manager.reconcile(uncertain), '200.2')
            self.assertEqual(client.conversations_replies.call_args.kwargs['ts'], '100.1')
            client.conversations_history.assert_not_called()

    def test_expired_followup_leaves_original_current_in_health_and_team_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp); config = {'team_id': 'T1', 'channel_id': 'C1', 'owner_user_id': 'U1'}
            schedule = Schedules(home, owner_key(config)).save('', '', dict(
                title='Watch', request='Inspect', weekdays=list('0123456'), time='10:00', timezone='UTC',
                enabled=True, catch_up_hours='6'), 'schedule')['schedule']
            due = datetime.now(timezone.utc).replace(hour=10, minute=0, second=0, microsecond=0)+timedelta(days=1)
            db = DigestStore(home/'scheduled'); self.addCleanup(db.close)
            base = dict(scope=scope(config), day=due.date().isoformat(), schedule_id=schedule['id'], revision=1)
            db.save(dict(base, key='run', status='sent', created=due.timestamp(), report={'findings': [], 'blockers': [], 'coverage': 'All.'}), due)
            db.save(dict(base, key='run:followup', status='expired', recovery_of='run', created=due.timestamp()+1800,
                         error_summary='The recovery window closed before this check finished.'), due+timedelta(hours=1))
            check = Health(home, config).automations(due+timedelta(hours=7))[0]
            self.assertEqual(check['status'], 'sent')
            item = TeamStatus(home, config).status(due+timedelta(hours=7))['agents'][0]['assignments'][0]
            self.assertEqual((item['last_status'], item['error']), ('sent', ''))


class DueSoonTests(unittest.TestCase):
    def test_reported_finding_is_repeated_once_near_its_date_for_any_monitor(self):
        for title, key in (('Renewal watch', 'service:renewal'), ('Deadline watch', 'grant:deadline')):
            soon = finding(key, 'v1', due='2030-01-05')
            later = finding(key+'-later', 'v1', due='2030-01-13')
            previous = {'findings': [soon, later], 'blockers': [], 'coverage': 'Mail.'}
            run = dict(key='k', title=title, created=NOW.timestamp())
            finish(run, {'findings': [soon, later], 'blockers': [], 'coverage': 'Mail.'}, previous, 'America/Los_Angeles')
            self.assertEqual(run['status'], 'ready')
            self.assertIn('Reminder, Sat Jan 5: Summary of '+key+'.', run['payload']['text'])
            self.assertNotIn(key+'-later', run['payload']['text'])
            again = dict(key='k2', title=title, created=NOW.timestamp()+86400)
            finish(again, {'findings': [soon, later], 'blockers': [], 'coverage': 'Mail.'}, run['report'])
            self.assertEqual(again['status'], 'quiet')
            self.assertNotIn('reminded', json.dumps(research_context(run['report'])))

    def test_due_must_be_a_date(self):
        from capo.assignment_reports import AssignmentReport
        with tempfile.TemporaryDirectory() as tmp:
            report = AssignmentReport(Path(tmp))
            report.record(findings=[finding('a', 'v1')], blockers=[], coverage='Checked.')
            with self.assertRaises(ValueError):
                report.record(findings=[finding('a', 'v1', due='next week')], blockers=[], coverage='Checked.')


class BriefingTests(unittest.TestCase):
    config = {'digest_briefings': [{'key': 'concerts', 'title': 'Music & Bay Area concerts', 'request': 'Find shows.'}]}

    def test_partial_briefing_keeps_verified_finding_and_names_gap(self):
        def run(provider, tools, request, directory, **kwargs):
            tools.call('monitor.report', dict(findings=[finding('show', '2030-01-05')],
                blockers=['Search budget ran out before other artists were checked.'], coverage='One calendar.'), operation_id='r')
            return {'status': 'partial'}
        with tempfile.TemporaryDirectory() as tmp, patch('capo.digest_briefings.research', side_effect=run):
            result = collect(Path(tmp), self.config, Path(tmp)/'run', {}, provider=object())
        self.assertIn('https://example.org/show', result['text'])
        self.assertIn('Check incomplete: Search budget ran out', result['text'])
        self.assertNotIn('could not complete', result['text'])
        self.assertEqual(len(result['findings']), 1)

    def test_failing_briefing_renders_fallback_and_records_error_privately(self):
        with tempfile.TemporaryDirectory() as tmp, patch('capo.digest_briefings.research', side_effect=RuntimeError('secret detail')):
            result = collect(Path(tmp), self.config, Path(tmp)/'run', {}, provider=object())
            failure = json.loads((Path(tmp)/'run/concerts/failure.json').read_text())
        self.assertEqual(result['text'], 'Music & Bay Area concerts\nI could not complete this check today.')
        self.assertEqual(failure, {'error': 'RuntimeError', 'message': 'secret detail'})


class DigestLayoutTests(unittest.TestCase):
    def test_briefings_precede_footer_escaping_once_and_caveat_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp); db = DigestStore(home); self.addCleanup(db.close)
            p = db.configure('owner', {'enabled': True})
            attention = [dict(id=f't{i}', task_id=f't{i}', kind='task', title=f'Task {i}', status='Unfinished', url='') for i in (1, 2)]
            TaskEvidence.annotate(attention, {'tasks': []})
            provider = Mock(); provider.call.return_value = dict(news=[], attention=['t1', 't2'], preparation='',
                                                                 preparation_event='', upcoming=[])
            evidence = dict(news=[], attention=attention, events=[], now=NOW.isoformat(), coverage=[])
            text = compose(evidence, p, {}, home, provider, briefings='Music & Bay Area concerts\n• A show.')['text']
        self.assertTrue(text.endswith('“digest settings.”'))
        self.assertLess(text.index('Music & Bay Area'), text.index('Reply with feedback'))
        self.assertEqual(text.count('not confirmed current'), 1)
        self.assertIn('Needs your attention (saved task status, not confirmed current)\n• Unfinished: Task 1', text)
        wire = slack_text(plain_text(text))
        self.assertIn('Music &amp; Bay Area', wire); self.assertNotIn('&amp;amp;', wire)


if __name__ == '__main__':
    unittest.main()
