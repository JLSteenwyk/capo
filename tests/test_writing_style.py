import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from capo.communication import writing_style
from capo.providers import Providers
from capo.contracts import TEXT,object_schema

class WritingStyleTests(unittest.TestCase):
    def test_private_guide_loaded_and_permissions_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'style.md'
            self.assertEqual(writing_style(path),'')
            path.write_text('Use warm, short sentences.');path.chmod(0o600)
            self.assertIn('warm, short',writing_style(path))
            self.assertIn('not code, tool arguments',writing_style(path))
            path.chmod(0o644)
            with self.assertRaises(ValueError):writing_style(path)

    def test_all_subscription_providers_receive_same_style_policy(self):
        for provider in ('claude','codex','grok'):
            with self.subTest(provider=provider),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp)
                def run(argv,cwd,directory,timeout,stdin=None):
                    if provider=='codex':
                        (directory/'result.json').write_text(json.dumps({'reply':'Done'}))
                        return ''
                    return json.dumps({'structured_output':{'reply':'Done'}})
                with patch('capo.communication.writing_style',return_value='STYLE POLICY\n'),patch('capo.providers.run_process',side_effect=run):
                    Providers(config={}, capacity=False).call(provider,'TASK',object_schema({'reply':TEXT}),root,root/'run')
                self.assertEqual((root/'run/prompt.txt').read_text(),'STYLE POLICY\nTASK')
