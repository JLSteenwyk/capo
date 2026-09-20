"""Configured eligibility for automatic draft delivery, never merge authority.

This structural heuristic supplements verified checks and independent acceptance;
neither routine nor feature eligibility proves semantic safety.
"""

import ast
import copy
import difflib
import re
from pathlib import Path

from .github import verified_workspace
from .improvement import verify_governance_changes
from .repository import git, safe_path
from .public_safety import check_candidate


_SENSITIVE = re.compile(
    r"(?:xox[baprs]-|xapp-|gh[pousr]_|github_pat_|(?:xai|sk)-[A-Za-z0-9_-]{16,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----|\b[UTCDW][A-Z0-9]{8,}\b)"
)
_RESTRICTED = re.compile(
    r"(?:^|[/_.-])(?:auth|authentication|authorization|security|credentials?|secrets?"
    r"|permissions?|policy|policies|governance|enforcement|config|settings|workflow"
    r"|dependencies|requirements|setup|conftest|sitecustomize|usercustomize)(?:$|[/_.-])"
)


def _structure(text):
    tree = ast.parse(text)
    signatures = []
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            imports.append(ast.dump(node, include_attributes=False))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            signature = copy.deepcopy(node)
            signature.body = []
            signatures.append(ast.dump(signature, include_attributes=False))

    class Bodies(ast.NodeTransformer):
        def visit_FunctionDef(self, node):
            node.body = [ast.Pass()]
            return node
        visit_AsyncFunctionDef = visit_FunctionDef

    # Module/class docstrings are prose, but executable module statements stay fixed.
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                node.body.pop(0)
    skeleton = ast.dump(Bodies().visit(tree), include_attributes=False)
    return skeleton, sorted(signatures), sorted(imports)


def _delta(before, after):
    lines = list(difflib.ndiff(before.splitlines(), after.splitlines()))
    return sum(line[:2] in ('+ ', '- ') for line in lines), '\n'.join(line[2:] for line in lines if line.startswith('+ '))


def assess(objective, change_scope='routine'):
    """Return eligible/reason without revealing private content in refusals."""
    def refuse(reason):
        return {'eligible': False, 'reason': reason}
    try:
        if change_scope not in ('routine','features'):
            return refuse('Unknown automatic change scope.')
        features=change_scope=='features'
        workspace, diff = verified_workspace(objective)
        check_candidate(objective,diff)
        names = git(workspace, 'diff', '--name-only', '-z', objective['base'], objective['accepted_tree'], raw=True).split('\0')
        names = [name for name in names if name]
        if not names or (not features and len(names) > 3):
            return refuse('Routine delivery is limited to three changed files.')
        verify_governance_changes(objective, [{'path': name} for name in names])
        source_lines = test_lines = doc_lines = 0
        for name in names:
            path = safe_path(name)
            if any(part.startswith('.') for part in path.parts) or _RESTRICTED.search(name.lower()):
                return refuse('Configuration or sensitive component changes need owner review.')
            if features and re.search(r'(?:^|[/_.-])migrations?(?:$|[/_.-])',name.lower()):
                return refuse('Data migrations need owner review.')
            if path.name.lower() in {'agents.md', 'claude.md', 'grok.md', 'pyproject.toml', '__init__.py', '__main__.py'}:
                return refuse('Configuration or package entry changes need owner review.')
            is_test = path.suffix == '.py' and (any(p in ('tests', 'test') for p in path.parts[:-1]) or path.name.startswith('test_'))
            if path.suffix not in ('.py', '.md'):
                return refuse('Routine delivery supports Python and Markdown changes only.')
            old_entry = git(workspace, 'ls-tree', objective['base'], '--', name)
            new_entry = git(workspace, 'ls-tree', objective['accepted_tree'], '--', name)
            if not new_entry or new_entry.split()[0] != '100644' or (old_entry and old_entry.split()[0] != '100644'):
                return refuse('Deleted, executable, or nonregular files need owner review.')
            if not features and not old_entry and path.suffix == '.py' and not is_test:
                return refuse('New source files need owner review.')
            old = git(workspace, 'show', objective['base'] + ':' + name, raw=True) if old_entry else ''
            new = git(workspace, 'show', objective['accepted_tree'] + ':' + name, raw=True)
            count, added = _delta(old, new)
            if _SENSITIVE.search(added) or str(Path.home()) in added:
                return refuse('Potential private information requires owner review.')
            if is_test:
                ast.parse(new)
                test_lines += count
            elif path.suffix == '.md':
                doc_lines += count
            else:
                ast.parse(new)
                if not features and _structure(old) != _structure(new):
                    return refuse('New functions, interfaces, imports, or module behavior need owner review.')
                source_lines += count
        if not features and (source_lines > 80 or test_lines > 250 or doc_lines > 80):
            return refuse('The change exceeds routine delivery size limits.')
        if features:return {'eligible':True,'reason':'Verified feature change within standing publication authority; protected components still require review.'}
        return {'eligible': True, 'reason': 'Small verified change with unchanged source interfaces; eligible for draft delivery.'}
    except (ValueError, KeyError, SyntaxError, OSError, TypeError):
        return refuse('The accepted candidate could not be verified for routine delivery.')
