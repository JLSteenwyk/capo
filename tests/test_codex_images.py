import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from capo.providers import Providers
from capo.contracts import object_schema,TEXT


class CodexImageTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.home=Path(tmp.name)
        self.directory=self.home/'attempt';self.raw=b'\x89PNG\r\n\x1a\nsynthetic fixture'
        self.image={'media_type':'image/png','data':base64.b64encode(self.raw).decode()}

    def call(self,images):
        return Providers(config={},capacity=False).call('codex','Describe the screenshot',object_schema({'answer':TEXT}),self.home,self.directory,images=images)

    def test_images_staged_privately_passed_as_flags_and_removed(self):
        staged=[]
        def run(name,argv,cwd,directory,timeout,prompt):
            self.assertEqual(name,'Codex');self.assertEqual(argv[argv.index('--sandbox')+1],'read-only')
            self.assertEqual(argv[-1],'-');self.assertNotIn(self.image['data'],prompt)
            for arg in argv:
                if arg.startswith('--image='):
                    p=Path(arg.split('=',1)[1]);staged.append(p)
                    self.assertEqual(p.read_bytes(),self.raw)
                    self.assertEqual(p.stat().st_mode & 0o777,0o600)
                    self.assertEqual(p.parent.stat().st_mode & 0o777,0o700)
            (directory/'result.json').write_text(json.dumps({'answer':'Image evidence'}))
            return ''
        with patch('capo.providers.run_cli',side_effect=run):
            self.assertEqual(self.call([self.image,self.image]),{'answer':'Image evidence'})
        self.assertEqual(len(staged),2)
        self.assertTrue(all(not p.exists() for p in staged))

    def test_invalid_inputs_do_not_invoke_provider(self):
        for images in ([self.image]*4,[dict(self.image,data='not base64')],[dict(self.image,media_type='text/plain')],
                       [dict(self.image,media_type='image/jpeg')],[dict(self.image,data=base64.b64encode(b'x'*4_000_001).decode())]):
            with self.subTest(images_count=len(images)),patch('capo.providers.run_cli') as run:
                with self.assertRaises(ValueError):self.call(images)
                run.assert_not_called()

    def test_provider_failure_also_cleans_staged_images(self):
        with patch('capo.providers.run_cli',side_effect=TimeoutError()),self.assertRaises(TimeoutError):self.call([self.image])
        self.assertEqual(list(self.directory.glob('image-input-*')),[])
