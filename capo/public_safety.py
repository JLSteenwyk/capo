"""Fail closed on recognizable private data before any GitHub publication.

Pattern checks supplement independent content review; they cannot identify every
private fact in arbitrary prose. Never include matched values in errors.
"""
import re
from pathlib import Path
from .repository import git

SENSITIVE = re.compile(
    r'(?:xox[baprs]-|xapp-|gh[pousr]_|github_pat_|(?:xai|sk)-[A-Za-z0-9_-]{16,}'
    r'|-----BEGIN [A-Z ]*PRIVATE KEY-----|\b[UTCDW][A-Z0-9]{8,}\b)'
)
PRIVATE_PATH = re.compile(r'(?:^|/)(?:artifacts|transcripts|credentials|personal-memory|request-memory|slack-images)(?:/|\.)|(?:\.sqlite3?|\.log|\.local\.json)$',re.I)


def check_text(text):
    if SENSITIVE.search(text) or str(Path.home()) in text:
        raise ValueError('Potential private information requires removal before publication')


def check_candidate(objective, diff):
    names=git(objective['workspace'],'diff','--name-only','-z',objective['base'],objective['accepted_tree']).split('\0')
    if any(PRIVATE_PATH.search(name) for name in names if name):
        raise ValueError('Private runtime artifacts must stay outside the repository')
    added='\n'.join(line[1:] for line in diff.splitlines() if line.startswith('+') and not line.startswith('+++'))
    check_text(added)
