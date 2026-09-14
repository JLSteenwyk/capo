import tempfile
import unittest
from datetime import datetime,timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from capo.attention import personal_tasks
from capo.capabilities import owner_key
from capo.tasks import Tasks
from capo.digest import DigestStore,scope,identity
from capo.digest_service import DigestManager
from test_tasks import fields


class AttentionTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.home=Path(tmp.name);self.config={'team_id':'T123','channel_id':'C123','owner_user_id':'U123'}
        self.now=datetime.fromisoformat('2030-01-07T10:00:00-08:00')
        self.client=Mock();self.client.chat_postMessage.return_value={'ts':'receipt'}
        self.service=SimpleNamespace(store=SimpleNamespace(home=self.home),config=self.config,client=self.client,bot_user_id='BOT')

    def manager(self,subdir):
        manager=DigestManager.__new__(DigestManager);manager.service=self.service
        manager.db=DigestStore(self.home/subdir);self.addCleanup(manager.db.close)
        return manager

    def run_record(self,manager,key,text,notice,status='ready'):
        return manager.db.create(dict(key=key,scope=scope(self.config),day='2030-01-07',status=status,
            created=self.now.timestamp(),deadline=self.now.timestamp()+3600,identity=identity(self.config),
            payload={'text':text,'news':[],'task_notices':[notice]}),self.now)

    def test_digest_then_hourly_deduplicates_and_changed_deadline_is_new(self):
        tasks=Tasks(self.home,owner_key(self.config))
        task=tasks.save('','',fields(due_at='2030-01-07T15:00:00-08:00'),'create')['task']
        evidence=personal_tasks(self.home,self.config,self.now)[0]
        notice={'id':evidence['id'],'day':evidence['notice_day'],'line':'• Task deadline'}
        digest=self.manager(Path('.'));hourly=self.manager(Path('heartbeat'))
        morning=self.run_record(digest,'morning','Good morning\n• Task deadline\nCalendar outlook',notice)
        digest.deliver(morning,self.now)
        later=self.run_record(hourly,'hourly','Needs your attention:\n• Task deadline',notice)
        hourly.deliver(later,self.now)
        self.assertEqual(self.client.chat_postMessage.call_count,1)
        self.assertEqual(hourly.db.get('hourly')['status'],'quiet')
        tasks.save(task['id'],'1',fields(due_at='2030-01-07T11:00:00-08:00'),'earlier')
        changed=personal_tasks(self.home,self.config,self.now)[0]
        self.assertNotEqual(changed['id'],notice['id'])
        notice=dict(notice,id=changed['id'])
        run=self.run_record(hourly,'changed','Needs your attention:\n• Task deadline',notice)
        hourly.deliver(run,self.now)
        self.assertEqual(self.client.chat_postMessage.call_count,2)

    def test_uncertain_delivery_reserves_notice_across_managers(self):
        notice={'id':'task1','day':'2030-01-07','line':'• Task deadline'}
        digest=self.manager(Path('.'));hourly=self.manager(Path('heartbeat'))
        self.client.chat_postMessage.side_effect=TimeoutError()
        first=self.run_record(digest,'morning','Good morning\n• Task deadline',notice)
        digest.deliver(first,self.now)
        self.assertEqual(digest.db.get('morning')['status'],'sending')
        self.client.chat_postMessage.side_effect=None
        second=self.run_record(hourly,'hourly','Needs your attention:\n• Task deadline',notice)
        hourly.deliver(second,self.now)
        self.assertEqual(self.client.chat_postMessage.call_count,1)
        self.assertEqual(hourly.db.get('hourly')['status'],'quiet')

    def test_waiting_and_explicit_reminders_have_distinct_coverage(self):
        tasks=Tasks(self.home,owner_key(self.config))
        tasks.save('','',fields(status='waiting',waiting_on='A colleague'),'waiting')
        tasks.save('','',fields(due_at='2030-01-07T15:00:00-08:00',remind_at='2030-01-07T14:00:00-08:00'),'reminder')
        tasks.save('','',fields(due_at='2030-02-01T15:00:00-08:00'),'later')
        self.assertEqual(len(personal_tasks(self.home,self.config,self.now)),2)
        self.assertEqual(len(personal_tasks(self.home,self.config,self.now,horizon_days=1,heartbeat=True)),1)
        other=dict(self.config,owner_user_id='U999')
        self.assertEqual(personal_tasks(self.home,other,self.now),[])
