"""Owner-configured automatic draft PR delivery for bounded, accepted work."""
import re
from pathlib import Path

from .github import GitHub, prepare, publish, remote_repository
from .repository import git
from .routine import assess, _SENSITIVE
from .runtime import exclusive


def deliver_routine(store, identifier, settings, gateway=None):
    if not (settings.get('allow_publication') and settings.get('auto_publish_routine')):
        return
    # Record an attempt before network activity. An interrupted delivery needs
    # reconciliation through the existing prepare/publish gateway, never a loop
    # that spends or posts again on every Slack tick.
    with exclusive(store.home):
        objective = store.get(identifier)
        if (objective.get('status') != 'completed' or objective.get('routine_delivery')
                or objective.get('publication')
                or Path(settings['path']).resolve() != Path(objective['repo']).resolve()):
            return
        verification = objective.get('verification', {})
        evidence = (verification.get('checks') and verification.get('reviews')
                    and all(row.get('passed') is True for row in verification['checks'])
                    and all(row.get('approved') is True for row in verification['reviews'])
                    and verification.get('decision', {}).get('accepted') is True)
        result = assess(objective) if evidence else {
            'eligible': False, 'reason': 'Verification or independent acceptance is incomplete.'}
        with store.db:
            store.db.execute('BEGIN IMMEDIATE')
            if (store.get(identifier) != objective
                    or store.followups(identifier) != objective.get('followups', [])):
                return
            objective['routine_delivery'] = dict(result, status='started' if result['eligible'] else 'review')
            store.save(objective, 'routine_delivery_assessed')
    if not result['eligible']:
        return
    gateway = gateway or GitHub(settings.get('github_auth', 'default'))
    try:
        repository = remote_repository(git(objective['repo'], 'remote', 'get-url', 'origin'))
        # Public metadata comes from the matching public issue, never transcripts,
        # worker summaries, shell commands, or private local configuration.
        title = 'Update existing behavior and regression coverage'
        source = objective.get('source', '')
        match = re.fullmatch(r'https://github.com/([^/]+/[^/]+)/issues/([1-9][0-9]*)', source)
        if match and match[1].lower() == repository.lower():
            title = gateway.issue(repository, int(match[2]))['title']
        else:
            source = ''
        if (not isinstance(title, str) or not title.strip() or len(title) > 120
                or '\n' in title or '\r' in title or _SENSITIVE.search(title)
                or str(Path.home()) in title):
            raise ValueError('Public title needs review')
        body = (f'{title.rstrip(".")}.\n\n'
                f'Validation: all {len(verification["checks"])} configured checks passed. '
                'Independent review and final acceptance passed.\n')
        if source:
            body += f'\nRelated issue: {source}\n'
        publication = prepare(store, identifier, repository,
                              settings.get('publication_base', 'main'), title=title, body=body)
        publish(store, identifier, publication['digest'], gateway)
        from .finalize import request_merge
        request_merge(store, identifier, settings)
        status = 'published'
    except (ValueError, RuntimeError, OSError, KeyError, TypeError):
        status = 'failed'
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        current = store.get(identifier)
        if current.get('routine_delivery', {}).get('status') == 'started':
            current['routine_delivery']['status'] = status
            store.save(current, 'routine_delivery_' + status)
