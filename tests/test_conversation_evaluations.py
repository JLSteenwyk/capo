import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from capo.evaluations.conversation_cases import CASES,ConversationWorld
from capo.evaluations.runner import run_case
from test_generalist_evaluations import step,finish as legacy_finish
from test_tasks import fields

def finish():
    return dict(legacy_finish(),arguments_json=json.dumps({'outcomes':[{'requirement':'Confirm the requested result','kind':'answer','status':'complete','evidence':[],'next_step':''}]}))


class ConversationEvaluationTests(unittest.TestCase):
    def test_followup_moves_work_event_and_carries_original_context(self):
        provider=Mock();provider.call.side_effect=[
            step('calendar.calendars',page_token=''),
            step('calendar.event',calendar_id='work',event_id='planning'),
            dict(finish(),reply='Work Planning is November 4, 2030, 10–11 a.m. Pacific.'),
            step('calendar.calendars',page_token=''),
            step('calendar.event',calendar_id='work',event_id='planning'),
            step('calendar.change',action='update',calendar_id='work',event_id='planning',title='Planning',location='Room 4',
                 start='2030-11-04T14:00:00-08:00',end='2030-11-04T15:00:00-08:00',all_day=False),finish()]
        with tempfile.TemporaryDirectory() as tmp:
            result=run_case(provider,'conversation_move',Path(tmp)/'run')
            turns=json.loads((Path(tmp)/'run/turns.json').read_text())
        self.assertTrue(result['automated_checks_passed'],result)
        self.assertIsNone(result['task_success'])
        self.assertEqual(turns[1]['request']['recent_messages'][0]['user'],CASES['conversation_move']['request'])
        self.assertIn('Work Planning',turns[1]['request']['recent_messages'][0]['capo'])

    def test_task_closed_in_place_on_owner_correction(self):
        actions=[step('mail.search',query='subject:Planning',page_size='10',page_token=''),step('mail.thread',id='thread1'),
            step('tasks.save',id='',expected_revision='',fields=fields(title='Reply to Morgan about Planning',sources=['thread1'])),
            dict(finish(),reply='Tracked your reply to Morgan.'),step('tasks.search',query='Morgan',status='all',cursor='')]
        def provider_call(*args,**kwargs):
            if actions:return actions.pop(0)
            payload=json.loads(args[1].splitlines()[-1])
            receipts=payload['receipts']
            if any(r.get('tool')=='tasks.save' for r in receipts):return finish()
            task=receipts[-1]['result']['tasks'][0]
            return step('tasks.save',id=task['id'],expected_revision=str(task['revision']),fields=fields(title='Reply to Morgan about Planning',sources=['thread1'],status='completed'))
        provider=Mock();provider.call.side_effect=provider_call
        with tempfile.TemporaryDirectory() as tmp:
            result=run_case(provider,'conversation_replied',Path(tmp)/'run')
        self.assertTrue(result['automated_checks_passed'],result)

    def test_grader_rejects_missing_duplicate_or_shifted_sessions(self):
        world=ConversationWorld(CASES['conversation_sessions'])
        for name,hour in [('Breakfast',8),('Keynote',10),('Lunch',12)]:
            from capo.evaluations.calendar_cases import event
            world.calendars['primary'][name]=event(name,name,hour)
            world.writes.append(('primary',name))
        world.advance()
        self.assertTrue(all(world.grade({'reply':'Added all three.','status':'reported_complete'}).values()))
        for mutate in (lambda w:w.calendars['primary'].pop('Lunch'),
                       lambda w:w.calendars['primary'].update(extra=copy.deepcopy(w.calendars['primary']['Lunch'])),
                       lambda w:w.calendars['primary']['Lunch']['start'].update(dateTime='2030-11-04T13:00:00-08:00')):
            broken=copy.deepcopy(world);mutate(broken)
            self.assertFalse(all(broken.grade({'reply':'All done.','status':'reported_complete'}).values()))

    def test_confident_reply_cannot_close_task_or_move_event(self):
        for name in ('conversation_replied','conversation_move','conversation_sessions'):
            provider=Mock();provider.call.return_value=finish()
            with tempfile.TemporaryDirectory() as tmp:
                result=run_case(provider,name,Path(tmp)/'run')
            self.assertFalse(result['automated_checks_passed'],name)
