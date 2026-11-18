import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from capo.providers import Providers


class CodexModelTests(unittest.TestCase):
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
