import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

from capo.contracts import object_schema
from capo.conversation import _write
from capo.recovery import RetryLater, RecoveryStopped
from capo.research_tools import ReadTool, ReadTools, research


FINISH = dict(action='finish', tool='', arguments_json='{}', reply='The result is ready.',
              document_title='', document='')
CREATE = dict(action='tool', tool='items.create', arguments_json='{}', reply='',
              document_title='', document='')


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.request = {'message': 'Create the requested item'}
        self.write = Mock(return_value={'id': 'item', 'created': True})
        self.original = ReadTool('items.create', 'Create authorized item', object_schema({}), self.write, True)
        self.extra = ReadTool('items.inspect', 'Inspect item', object_schema({}), Mock(return_value={}))

    def call(self, backend, catalog, now, request=None):
        with patch('capo.recovery.time.time', return_value=now):
            return research(backend, ReadTools(catalog), request or self.request, self.root)

    def test_catalog_update_preserves_write_and_exact_deferred_prompt(self):
        backend = Mock()
        backend.call.side_effect = [CREATE, TimeoutError(), FINISH]
        with self.assertRaises(RetryLater):
            self.call(backend, [self.original], 100)
        result = self.call(backend, [self.original, self.extra], 200)
        self.write.assert_called_once()
        self.assertEqual(result['receipts'][0]['result']['id'], 'item')
        self.assertEqual(backend.call.call_args_list[1].args[1], backend.call.call_args_list[2].args[1])
        self.assertEqual(backend.call.call_args_list[1].args[4].parent, backend.call.call_args_list[2].args[4].parent)

    def test_removed_tool_cannot_execute_from_frozen_provider_prompt(self):
        backend = Mock()
        backend.call.side_effect = [TimeoutError(), CREATE, FINISH]
        with self.assertRaises(RetryLater):
            self.call(backend, [self.original], 100)
        result = self.call(backend, [self.extra], 200)
        self.write.assert_not_called()
        self.assertIn('error', result['receipts'][0])
        data = json.loads(backend.call.call_args.args[1].splitlines()[-1])
        self.assertEqual([t['name'] for t in data['tools']], ['items.inspect'])

    def test_legacy_checkpoint_is_migrated_only_with_verified_original_prompt(self):
        backend = Mock()
        backend.call.side_effect = [TimeoutError(), FINISH]
        with self.assertRaises(RetryLater):
            self.call(backend, [self.original], 100)
        path = self.root/'checkpoint.json'
        state = json.loads(path.read_text())
        for key in ('version', 'request_identity', 'capability_identity'):
            del state[key]
        _write(path, state)
        saved = json.loads((self.root/'step-0/input.json').read_text())['prompt']
        (self.root/'step-0/input.json').unlink()
        attempt = self.root/'step-0/1'
        attempt.mkdir()
        (attempt/'prompt.txt').write_text('Synthetic style prefix.\n'+saved)
        with patch('capo.recovery.time.time', return_value=200):
            research(backend, ReadTools([self.original, self.extra]), self.request, self.root,
                     instructions='Updated chief guidance')
        self.assertEqual(json.loads(path.read_text())['version'], 2)
        self.assertEqual(backend.call.call_args.args[1], saved)

    def test_changed_request_cannot_reuse_old_receipts(self):
        backend = Mock()
        backend.call.side_effect = [CREATE, TimeoutError()]
        with self.assertRaises(RetryLater):
            self.call(backend, [self.original], 100)
        with self.assertRaises(RecoveryStopped):
            self.call(backend, [self.original, self.extra], 200, {'message': 'Create something else'})
        self.assertEqual(backend.call.call_count, 2)
        self.write.assert_called_once()

    def test_unverifiable_legacy_input_preserves_receipts_without_new_worker(self):
        backend = Mock()
        backend.call.side_effect = [CREATE, TimeoutError()]
        with self.assertRaises(RetryLater):
            self.call(backend, [self.original], 100)
        path = self.root/'checkpoint.json'
        state = json.loads(path.read_text())
        for key in ('version', 'request_identity', 'capability_identity'):
            del state[key]
        _write(path, state)
        with self.assertRaises(RecoveryStopped):
            self.call(backend, [self.extra], 200)
        self.assertEqual(json.loads(path.read_text()), state)
        self.assertEqual(backend.call.call_count, 2)
        self.write.assert_called_once()

    def test_tampered_saved_provider_input_does_not_execute(self):
        backend = Mock()
        backend.call.side_effect = TimeoutError()
        with self.assertRaises(RetryLater):
            self.call(backend, [self.original], 100)
        path = self.root/'step-0/input.json'
        state = json.loads(path.read_text())
        state['prompt'] += 'Changed input'
        _write(path, state)
        with self.assertRaises(RecoveryStopped):
            self.call(backend, [self.original, self.extra], 200)
        self.assertEqual(backend.call.call_count, 1)

    def test_uncertain_write_survives_catalog_update_without_replay(self):
        self.write.side_effect = KeyboardInterrupt()
        backend = Mock()
        backend.call.side_effect = [CREATE, CREATE, FINISH]
        with self.assertRaises(KeyboardInterrupt):
            self.call(backend, [self.original], 100)
        result = self.call(backend, [self.original, self.extra], 200)
        self.write.assert_called_once()
        self.assertTrue(result['receipts'][0]['uncertain'])
        self.assertIn('reconciled', result['receipts'][1]['error'])

    def test_midnight_resume_discards_stale_write_and_refreshes_time(self):
        backend=Mock();backend.call.side_effect=[TimeoutError(),CREATE,FINISH]
        before=datetime.fromisoformat('2026-09-15T23:59:00-07:00')
        after=datetime.fromisoformat('2026-09-16T00:01:00-07:00')
        with patch('capo.research_tools.datetime',wraps=datetime) as clock:
            clock.now.return_value=before
            with self.assertRaises(RetryLater):self.call(backend,[self.original],100)
            clock.now.return_value=after
            result=self.call(backend,[self.original],200)
        self.write.assert_not_called()
        self.assertEqual(backend.call.call_args_list[0].args[1],backend.call.call_args_list[1].args[1])
        self.assertIn('Current local time: '+after.isoformat(),backend.call.call_args.args[1])
        data=json.loads(backend.call.call_args.args[1].splitlines()[-1])
        self.assertEqual(data['request_started_at'],before.isoformat())
        self.assertIn('discarded',result['receipts'][0]['error'])

    def test_new_step_refreshes_clock_without_changing_source_context(self):
        backend=Mock()
        before=datetime.fromisoformat('2026-09-15T10:00:00-07:00')
        after=datetime.fromisoformat('2026-09-15T11:00:00-07:00')
        request={'message':'Use tomorrow from this older email',
                 'source':{'sent_at':'2026-09-10T15:00:00Z','text':'Tomorrow'}}
        with patch('capo.research_tools.datetime',wraps=datetime) as clock:
            clock.now.return_value=before
            def inspect():
                clock.now.return_value=after
                return {'source_date':'2026-09-10','relative_text':'Tomorrow'}
            tool=ReadTool('source.inspect','Inspect source',object_schema({}),inspect)
            backend.call.side_effect=[dict(CREATE,tool='source.inspect'),FINISH]
            self.call(backend,[tool],100,request)
        prompt=backend.call.call_args.args[1]
        data=json.loads(prompt.splitlines()[-1])
        self.assertIn('Current local time: '+after.isoformat(),prompt)
        self.assertEqual(data['request'],request)
        self.assertEqual(data['request_started_at'],before.isoformat())

    def test_owner_message_timestamp_is_source_metadata(self):
        from capo.slack import owner_message_record
        self.assertEqual(owner_message_record('Tomorrow',{'ts':'0'})['sent_at'],'1970-01-01T00:00:00+00:00')
        self.assertNotIn('sent_at',owner_message_record('Tomorrow',{'ts':'unknown'}))

    def test_date_revalidation_cannot_reset_budget(self):
        backend=Mock()
        dates=iter([datetime.fromisoformat('2026-09-16T00:01:00-07:00'),
                    datetime.fromisoformat('2026-09-17T00:01:00-07:00')])
        with patch('capo.research_tools.datetime',wraps=datetime) as clock:
            clock.now.return_value=datetime.fromisoformat('2026-09-15T23:59:00-07:00')
            def decide(*args,**kwargs):
                clock.now.return_value=next(dates)
                return CREATE
            backend.call.side_effect=decide
            result=research(backend,ReadTools([self.original]),self.request,self.root,max_calls=1)
        self.write.assert_not_called()
        self.assertEqual(backend.call.call_count,2)
        self.assertIn('execution limit',result['reply'])
        self.assertEqual(len(result['receipts']),2)
        self.assertEqual(research(backend,ReadTools([self.original]),self.request,self.root,max_calls=1),result)

    def test_correction_at_mutation_boundary_clears_unexecuted_intent(self):
        backend=Mock();backend.call.return_value=CREATE
        updates=Mock(side_effect=[None,None,'new-owner-event'])
        result=research(backend,ReadTools([self.original]),self.request,self.root,owner_update=updates)
        self.write.assert_not_called()
        self.assertEqual(result['status'],'superseded')
        state=json.loads((self.root/'checkpoint.json').read_text())
        self.assertIsNone(state['pending'])
        self.assertEqual(state['receipts'],[])
        self.assertEqual(research(backend,ReadTools([self.original]),self.request,self.root),result)

    def test_correction_before_finish_preserves_completed_action_receipt(self):
        backend=Mock();backend.call.side_effect=[CREATE,FINISH]
        result=research(backend,ReadTools([self.original]),self.request,self.root,
            owner_update=lambda:'new-owner-event' if backend.call.call_count==2 else None)
        self.write.assert_called_once()
        self.assertEqual(result['status'],'superseded')
        self.assertEqual(result['receipts'][0]['result']['id'],'item')


if __name__ == '__main__':
    unittest.main()
