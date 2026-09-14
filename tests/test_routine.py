import tempfile
import unittest
from pathlib import Path

from capo.repository import git
from capo.routine import assess


class RoutineCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name)
        git(self.repo, 'init', '-b', 'main')
        git(self.repo, 'config', 'user.name', 'Test')
        git(self.repo, 'config', 'user.email', 'test@example.invalid')
        self.original = 'def str2bool(value):\n    """Parse a boolean."""\n    value = value.lower()\n    return value in ("true", "t", "1")\n'
        (self.repo / 'parser.py').write_text(self.original)
        (self.repo / 'tests').mkdir()
        (self.repo / 'tests/test_parser.py').write_text('def test_parser():\n    assert True\n')
        git(self.repo, 'add', '.')
        git(self.repo, 'commit', '-m', 'fixture')
        self.objective = {'status': 'completed', 'workspace': str(self.repo), 'repo': str(self.repo), 'base': git(self.repo, 'rev-parse', 'HEAD')}

    def tearDown(self):
        self.temp.cleanup()

    def accepted(self, changes):
        for name, content in changes.items():
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if content is None:
                path.unlink()
            else:
                path.write_text(content)
        git(self.repo, 'add', '-A')
        self.objective['accepted_tree'] = git(self.repo, 'write-tree')
        return assess(self.objective)

    def test_membership_and_docstring_with_new_tests(self):
        result = self.accepted({'parser.py': self.original.replace('Parse a boolean.', 'Parse true or yes spellings.').replace('"1")', '"1", "yes", "y")'), 'tests/test_new.py': 'def test_yes():\n    assert "yes".lower() == "yes"\n'})
        self.assertTrue(result['eligible'], result)

    def test_new_function_or_interface_rejected(self):
        for replacement in [self.original + '\ndef extra():\n    return 1\n', self.original.replace('value):', 'value, default=False):'), self.original.replace('    value =', '    import os\n    value =')]:
            with self.subTest(replacement=replacement):
                self.assertFalse(self.accepted({'parser.py': replacement})['eligible'])

    def test_nested_function_rejected(self):
        self.assertFalse(self.accepted({'parser.py': self.original.replace('    value =', '    def helper():\n        return 1\n    value =')})['eligible'])

    def test_new_source_and_config_rejected(self):
        self.assertFalse(self.accepted({'new.py': 'value = 1\n'})['eligible'])
        (self.repo / 'new.py').unlink()
        self.assertFalse(self.accepted({'settings.py': 'value = 1\n'})['eligible'])

    def test_same_tree_and_unverified_change_rejected(self):
        self.assertFalse(self.accepted({})['eligible'])
        self.assertTrue(self.accepted({'parser.py': self.original.replace('"1")', '"1", "yes")')})['eligible'])
        (self.repo / 'parser.py').write_text(self.original + '# later change\n')
        self.assertFalse(assess(self.objective)['eligible'])

    def test_secrets_rejected_without_disclosure(self):
        marker = 'xoxb-' + 'synthetic-not-a-real-token'
        result = self.accepted({'parser.py': self.original + '# ' + marker + '\n'})
        self.assertFalse(result['eligible'])
        self.assertNotIn(marker, result['reason'])

    def test_source_limit(self):
        self.assertFalse(self.accepted({'parser.py': self.original + '\n'.join('# comment' for _ in range(81))})['eligible'])

    def test_deleted_file_rejected(self):
        self.assertFalse(self.accepted({'parser.py': None})['eligible'])

    def test_symlink_rejected(self):
        (self.repo / 'parser.py').unlink()
        (self.repo / 'parser.py').symlink_to('tests/test_parser.py')
        self.assertFalse(self.accepted({})['eligible'])

    def test_module_execution_rejected(self):
        self.assertFalse(self.accepted({'parser.py': self.original + '\nstr2bool("yes")\n'})['eligible'])

    def test_governance_rejected(self):
        self.objective['kind'] = 'self_improvement'
        self.assertFalse(self.accepted({'capo/runtime.py': self.original})['eligible'])

    def test_decorator_rejected(self):
        self.assertFalse(self.accepted({'parser.py': '@staticmethod\n' + self.original})['eligible'])

    def test_executable_mode_rejected(self):
        (self.repo / 'parser.py').chmod(0o755)
        self.assertFalse(self.accepted({})['eligible'])

    def test_file_count_limit(self):
        self.assertFalse(self.accepted({f'docs/{index}.md': 'Explanation\n' for index in range(4)})['eligible'])

    def test_test_line_limit(self):
        self.assertFalse(self.accepted({'tests/test_large.py': '\n'.join('# comment' for _ in range(251))})['eligible'])
