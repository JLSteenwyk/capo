import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from capo.providers import Providers


class CodexModelTests(unittest.TestCase):
    def test_recovered_cli_errors_do_not_discard_a_fresh_successful_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            directory = root/'attempt'
            directory.mkdir()
            (directory/'result.json').write_text('{"answer":"old"}')

            def run(provider, argv, cwd, directory, timeout, prompt):
                self.assertFalse((directory/'result.json').exists())
                (directory/'result.json').write_text('{"answer":"new"}')
                return '\n'.join(json.dumps(event) for event in (
                    {'type': 'error', 'message': 'Reconnecting... 401 Unauthorized'},
                    {'type': 'turn.completed'}))

            with patch('capo.providers.run_cli', side_effect=run):
                self.assertEqual(Providers(config={}, capacity=False).call(
                    'codex', 'Synthetic request', {}, root, directory), {'answer': 'new'})

    def test_terminal_failure_rejects_even_a_written_result(self):
        from capo.providers import AuthenticationError
        for write_result in (False, True):
            with self.subTest(write_result=write_result), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)

                def run(provider, argv, cwd, directory, timeout, prompt):
                    if write_result:
                        (directory/'result.json').write_text('{"answer":"incomplete"}')
                    return json.dumps({'type': 'turn.failed', 'error': {'message': '401 Unauthorized'}})

                with patch('capo.providers.run_cli', side_effect=run):
                    with self.assertRaises(AuthenticationError):
                        Providers(config={}, capacity=False).call('codex', 'Synthetic', {}, root, root/'attempt')

    def test_stale_result_cannot_mask_an_error_only_stream(self):
        from capo.providers import AuthenticationError
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'result.json').write_text('{"answer":"stale"}')
            with patch('capo.providers.run_cli', return_value=json.dumps(
                    {'type': 'error', 'message': '401 Unauthorized'})):
                with self.assertRaises(AuthenticationError):
                    Providers(config={}, capacity=False).call('codex', 'Synthetic', {}, root, root)

    def test_codex_pins_model_and_defaults_effort_but_preserves_explicit_effort(self):
        for effort in (None, 'low', 'medium', 'high', 'xhigh', 'max'):
            with self.subTest(effort=effort), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)

                def run(provider, argv, cwd, directory, timeout, prompt):
                    self.assertEqual(provider, 'Codex')
                    self.assertEqual(argv[argv.index('--model') + 1], 'gpt-6.2-sol')
                    self.assertEqual(argv[argv.index('--config') + 1],
                                     'model_reasoning_effort=' + json.dumps(effort or 'medium'))
                    self.assertEqual(argv[argv.index('--sandbox') + 1], 'read-only')
                    self.assertIn('--output-schema', argv)
                    self.assertEqual(argv[-1], '-')
                    (directory / 'result.json').write_text('{"answer":"done"}')
                    return ''

                with patch('capo.providers.run_cli', side_effect=run):
                    result = Providers(config={}, capacity=False, effort=effort).call(
                        'codex', 'Synthetic task', {}, root, root / 'attempt')
                self.assertEqual(result, {'answer': 'done'})
