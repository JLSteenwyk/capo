import tempfile
import unittest
from datetime import datetime,timezone,timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from capo.assignment_reports import finish
from capo.digest import DigestStore,identity,scope
from capo.digest_service import DigestManager
from capo.finding_notices import notice


def finding(version='failing',key='project/pr/7',source='https://example.invalid/project/pull/7',**extra):
    return dict(dict(key=key,version=version,source=source,summary='Project PR 7 needs review.'),**extra)


class FindingNoticeTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.home=Path(tmp.name)
        self.now=datetime(2030,1,3,20,tzinfo=timezone.utc)
        self.config=dict(team_id='T1',channel_id='C1',owner_user_id='U1')
        self.client=Mock();self.client.chat_postMessage.return_value={'ok':True,'ts':'receipt'}
        self.service=SimpleNamespace(store=SimpleNamespace(home=self.home),config=self.config,client=self.client,bot_user_id='BOT')

    def manager(self,where):
        manager=DigestManager.__new__(DigestManager);manager.service=self.service
        manager.db=DigestStore(self.home/where);self.addCleanup(manager.db.close)
        return manager

    def prepare(self,manager,key,items,previous=None):
        run=dict(key=key,scope=scope(self.config),day=self.now.date().isoformat(),created=self.now.timestamp(),
                 deadline=self.now.timestamp()+3600,identity=identity(self.config),title='Scheduled check')
        finish(run,dict(findings=items,blockers=[],coverage='Synthetic inspected sources.'),previous or {})
        return manager.db.create(run,self.now)

    def test_overlapping_roles_suppress_same_source_and_version_but_deliver_changes(self):
        coding=self.manager(Path('scheduled'));research=self.manager(Path('.'))
        coding.deliver(self.prepare(coding,'coding',[finding()]),self.now)
        research.deliver(self.prepare(research,'research',[finding(key='different-role-key')]),self.now)
        self.assertEqual(self.client.chat_postMessage.call_count,1)
        self.assertEqual(research.db.get('research')['status'],'quiet')
        research.deliver(self.prepare(research,'changed',[finding(version='passing')]),self.now)
        self.assertEqual(self.client.chat_postMessage.call_count,2)
        self.assertEqual(research.db.get('changed')['status'],'sent')

    def test_partial_dedup_renders_remaining_items_and_keeps_original_report(self):
        manager=self.manager(Path('scheduled'))
        manager.deliver(self.prepare(manager,'first',[finding()]),self.now)
        fresh=finding(key='other',source='https://example.invalid/project/pull/8',version='new',summary='PR 8 is ready.')
        manager.deliver(self.prepare(manager,'second',[finding(),fresh]),self.now)
        text=self.client.chat_postMessage.call_args.kwargs['text']
        self.assertIn('PR 8 is ready',text);self.assertNotIn('PR 7 needs review',text)
        saved=manager.db.get('second')
        self.assertEqual(len(saved['report']['findings']),2)
        self.assertEqual(len(saved['payload']['finding_notices']),1)

    def test_uncertain_send_reserves_notice_without_rewriting_original_payload(self):
        manager=self.manager(Path('scheduled'));other=self.manager(Path('heartbeat'))
        self.client.chat_postMessage.side_effect=TimeoutError()
        manager.deliver(self.prepare(manager,'first',[finding()]),self.now)
        original=manager.db.get('first');self.assertEqual(original['status'],'sending')
        self.client.chat_postMessage.side_effect=None
        other.deliver(self.prepare(other,'second',[finding()]),self.now)
        self.assertEqual(self.client.chat_postMessage.call_count,1)
        self.assertEqual(manager.db.get('first')['wire_text'],original['wire_text'])
        self.assertEqual(other.db.get('second')['status'],'quiet')

    def test_deadline_reminder_is_distinct_from_earlier_change_notice(self):
        manager=self.manager(Path('scheduled'))
        item=finding(due='2030-01-05')
        manager.deliver(self.prepare(manager,'first',[item]),self.now)
        manager.deliver(self.prepare(manager,'reminder',[item],dict(findings=[item])),self.now)
        self.assertEqual(self.client.chat_postMessage.call_count,2)
        self.assertIn('Reminder',self.client.chat_postMessage.call_args.kwargs['text'])
        manager.deliver(self.prepare(manager,'other-reminder',[item],dict(findings=[item])),self.now)
        self.assertEqual(self.client.chat_postMessage.call_count,2)
        self.assertNotEqual(notice(item,'change')['id'],notice(item,'reminder')['id'])

    def test_rejected_send_does_not_reserve_and_owner_scope_isolated(self):
        manager=self.manager(Path('scheduled'))
        first=self.prepare(manager,'rejected',[finding()]);first['status']='failed';manager.db.save(first,self.now)
        manager.deliver(self.prepare(manager,'retry',[finding()]),self.now)
        self.assertEqual(self.client.chat_postMessage.call_count,1)
        other=dict(self.config,owner_user_id='U2');self.service.config=other;self.config=other
        manager.deliver(self.prepare(manager,'other-owner',[finding()]),self.now)
        self.assertEqual(self.client.chat_postMessage.call_count,2)

    def test_old_uncertain_send_still_reserves_but_old_delivered_notice_expires(self):
        manager=self.manager(Path('scheduled'));other=self.manager(Path('heartbeat'))
        run=self.prepare(manager,'old-uncertain',[finding()])
        run['status']='delivery_unknown';manager.db.save(run,self.now-timedelta(days=8))
        other.deliver(self.prepare(other,'new-check',[finding()]),self.now)
        self.client.chat_postMessage.assert_not_called()
        self.assertEqual(other.db.get('new-check')['status'],'quiet')
        run['status']='sent';manager.db.save(run,self.now-timedelta(days=8))
        other.deliver(self.prepare(other,'later-check',[finding()]),self.now)
        self.client.chat_postMessage.assert_called_once()
