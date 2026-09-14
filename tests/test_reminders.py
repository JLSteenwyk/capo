import tempfile
import unittest
from datetime import datetime,timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from capo.reminders import ReminderManager
from capo.tasks import Tasks
from capo.capabilities import owner_key
from test_tasks import fields


class ReminderTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.home=Path(tmp.name);self.config={'team_id':'T123','channel_id':'C123','owner_user_id':'U123'}
        self.client=Mock();self.client.chat_postMessage.return_value={'ok':True,'ts':'synthetic-ts'}
        self.client.conversations_history.return_value={'messages':[]}
        self.service=SimpleNamespace(store=SimpleNamespace(home=self.home),config=self.config,client=self.client,bot_user_id='BOT')
        self.manager=ReminderManager(self.service);self.addCleanup(self.manager.db.close)
        self.tasks=Tasks(self.home,owner_key(self.config))
        self.now=datetime.fromisoformat('2026-09-14T09:00:00-07:00')

    def test_due_rescheduled_and_restart(self):
        task=self.tasks.save('','',fields(remind_at='2026-09-14T09:01:00-07:00'),'create')['task']
        self.manager.tick(self.now);self.client.chat_postMessage.assert_not_called()
        self.tasks.save(task['id'],'1',fields(remind_at='2026-09-14T10:00:00-07:00'),'reschedule')
        self.manager.tick(self.now+timedelta(minutes=2));self.client.chat_postMessage.assert_not_called()
        self.manager.tick(self.now+timedelta(hours=1));self.assertEqual(self.client.chat_postMessage.call_count,1)
        restarted=ReminderManager(self.service);self.addCleanup(restarted.db.close)
        restarted.tick(self.now+timedelta(hours=2));self.assertEqual(self.client.chat_postMessage.call_count,1)

    def test_missed_schedule_delivers_once_and_cancelled_task_does_not(self):
        self.tasks.save('','',fields(remind_at='2026-09-13T09:00:00-07:00'),'missed')
        t=self.tasks.save('','',fields(remind_at='2026-09-14T08:00:00-07:00'),'cancel')['task']
        self.tasks.save(t['id'],'1',fields(status='cancelled',remind_at=t['remind_at']),'cancel2')
        self.manager.tick(self.now);self.manager.tick(self.now)
        self.assertEqual(self.client.chat_postMessage.call_count,1)
        self.assertIn('Missed reminder',self.client.chat_postMessage.call_args.kwargs['text'])

    def test_uncertain_post_reconciles_without_duplicate(self):
        import html
        self.tasks.save('','',fields(remind_at='2026-09-14T09:00:00-07:00'),'create')
        self.client.chat_postMessage.side_effect=TimeoutError()
        self.manager.tick(self.now)
        import json
        run=json.loads(self.manager.db.db.execute('SELECT data FROM runs').fetchone()[0])
        self.client.conversations_history.return_value={'messages':[{'bot_id':'b','user':'BOT',
            'ts':'receipt','text':html.escape(run['payload']['text'],quote=False),
            'client_msg_id':run['marker']}]}
        self.manager.tick(self.now+timedelta(minutes=2))
        self.assertEqual(self.client.chat_postMessage.call_count,1)
        self.assertEqual(self.manager.db.get(run['key'])['status'],'sent')
