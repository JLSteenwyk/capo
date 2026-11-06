import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
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
