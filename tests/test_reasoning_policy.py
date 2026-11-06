import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from capo.reasoning_policy import settings,selected,recovery
from capo.recovery import RecoveringProvider
from capo.capacity import Capacity
from capo.contracts import object_schema


class PolicyTests(unittest.TestCase):
    def test_codex_default_explicit_choices_and_pinned_resume(self):
        self.assertEqual(settings()['primary'],'codex')
        self.assertIsNone(settings()['fallback'])
        self.assertEqual(selected({'reasoning':{'primary':'claude'}}),'claude')
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp);(directory/'checkpoint.json').write_text(json.dumps({'reasoning_provider':'claude'}))
            self.assertEqual(selected({},directory),'claude')
            self.assertEqual(selected({},directory,explicit='grok'),'grok')

    def test_one_distinct_fallback_only(self):
        for value in ({'primary':'unknown'},{'primary':'codex','fallback':'codex'},{'fallback':['claude','grok']}):
            with self.assertRaises((ValueError,TypeError)):settings({'reasoning':value})
        self.assertEqual(recovery({'reasoning':{'fallback':'claude'}})['fallbacks'],{'codex':['claude']})

    def test_preferred_provider_not_reranked_by_headroom(self):
        backend=Mock();backend.capacity=Mock(spec=Capacity)
        backend.capacity.choose.return_value=('codex',{'reason':'preferred_worker'})
        backend.call.return_value={}
        with tempfile.TemporaryDirectory() as tmp:
            wrapper=RecoveringProvider(backend,recovery({'reasoning':{'fallback':'claude'}}))
            wrapper.call('codex','prompt',object_schema({}),Path(tmp),Path(tmp)/'step')
        backend.capacity.choose.assert_called_once_with('codex',['codex'])
        self.assertEqual(backend.call.call_args.args[0],'codex')

    def test_exhausted_primary_uses_only_configured_fallback(self):
        from capo.recovery import RateLimited
        backend=Mock();backend.capacity=Mock(spec=Capacity)
        backend.capacity.choose.side_effect=[RateLimited(9999999999),('claude',{'reason':'available_fallback'})]
        backend.call.return_value={}
        with tempfile.TemporaryDirectory() as tmp:
            RecoveringProvider(backend,recovery({'reasoning':{'fallback':'claude'}})).call('codex','prompt',object_schema({}),Path(tmp),Path(tmp)/'step')
        self.assertEqual(backend.call.call_args.args[0],'claude')
        self.assertEqual(backend.capacity.choose.call_args.args,('claude',['claude']))

    def test_existing_request_recovery_policy_is_not_rewritten(self):
        from capo.reasoning_policy import request_recovery
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp);step=directory/'step-0';step.mkdir()
            old={'max_attempts':3,'base_delay':30,'fallbacks':{'claude':['codex']}}
            (step/'recovery.json').write_text(json.dumps({'policy':old}))
            self.assertEqual(request_recovery({},directory),old)


class DirectPolicyTests(unittest.TestCase):
    config={'reasoning':{'fallback':'claude'}}

    def invoke(self,backend,directory,primary='codex',config=None,images=None):
        from capo.reasoning_policy import direct_call
        return direct_call(backend,primary,'same prompt',object_schema({}),directory/'cwd',directory/'call',
                           config=self.config if config is None else config,images=images)

    def test_success_stays_primary_and_outage_uses_only_one_configured_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);backend=Mock();backend.call.return_value={}
            self.invoke(backend,root/'success')
            self.assertEqual([c.args[0] for c in backend.call.call_args_list],['codex'])
            backend.reset_mock();backend.call.side_effect=[ConnectionError('private provider detail'),{}]
            self.invoke(backend,root/'outage')
            self.assertEqual([c.args[0] for c in backend.call.call_args_list],['codex','claude'])
            self.assertEqual(backend.call.call_args_list[0].args[1:3],backend.call.call_args_list[1].args[1:3])
            receipt=json.loads((root/'outage/call/fallback-receipt.json').read_text())
            self.assertEqual(receipt['selected'],'claude')
            self.assertNotIn('private provider detail',json.dumps(receipt))

    def test_authentication_permission_and_format_errors_do_not_switch(self):
        from capo.providers import AuthenticationError,StructuredOutputError
        for error in (AuthenticationError('login'),PermissionError('denied'),StructuredOutputError('invalid')):
            with self.subTest(error=type(error).__name__),tempfile.TemporaryDirectory() as tmp:
                backend=Mock();backend.call.side_effect=error
                with self.assertRaises(type(error)):self.invoke(backend,Path(tmp))
                self.assertEqual(backend.call.call_count,1)

    def test_no_fallback_for_pinned_other_provider_or_unconfigured_policy(self):
        for primary,config in [('grok',self.config),('codex',{})]:
            with self.subTest(primary=primary),tempfile.TemporaryDirectory() as tmp:
                backend=Mock();backend.call.side_effect=ConnectionError()
                with self.assertRaises(ConnectionError):self.invoke(backend,Path(tmp),primary,config)
                self.assertEqual(backend.call.call_count,1)

    def test_images_are_identical_and_incompatible_fallback_is_not_used(self):
        images=[{'media_type':'image/png','data':'synthetic'}]
        with tempfile.TemporaryDirectory() as tmp:
            backend=Mock();backend.call.side_effect=[FileNotFoundError(),{}]
            self.invoke(backend,Path(tmp),images=images)
            self.assertEqual([c.kwargs['images'] for c in backend.call.call_args_list],[images,images])
        with tempfile.TemporaryDirectory() as tmp:
            backend=Mock();backend.call.side_effect=ConnectionError()
            with self.assertRaises(ConnectionError):self.invoke(backend,Path(tmp),config={'reasoning':{'fallback':'grok'}},images=images)
            self.assertEqual(backend.call.call_count,1)

    def test_original_time_budget_cannot_be_extended_and_deadline_is_restored(self):
        with tempfile.TemporaryDirectory() as tmp:
            clock=[100.0];backend=Mock();backend.timeout=5;backend.deadline=None
            def fail(*args,**kwargs):
                self.assertEqual(backend.deadline,105.0)
                clock[0]=106.0
                raise TimeoutError()
            backend.call.side_effect=fail
            with patch('time.time',side_effect=lambda:clock[0]):
                with self.assertRaises(TimeoutError):self.invoke(backend,Path(tmp))
            self.assertEqual(backend.call.call_count,1)
            self.assertIsNone(backend.deadline)
