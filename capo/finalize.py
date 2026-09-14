"""Finish explicitly authorized deliveries without bypassing GitHub checks."""
import time

from .github import GitHub
from .repository import git
from .runtime import exclusive


def request_merge(store, identifier, settings):
    if not (settings.get('allow_publication') and settings.get('merge_after_approval')):
        return False
    with exclusive(store.home):
        objective = store.get(identifier)
        publication = objective.get('publication', {})
        if publication.get('status') != 'published':
            raise ValueError('Publish the reviewed change first')
        if not objective.get('merge_delivery'):
            objective['merge_delivery'] = {'status': 'waiting', 'commit': publication['payload']['commit']}
            store.save(objective, 'merge_authorized')
    return True


def finish_delivery(store, identifier, settings, gateway=None):
    if not (settings.get('allow_publication') and settings.get('merge_after_approval')):
        return
    gateway = gateway or GitHub(settings.get('github_auth', 'default'))
    with exclusive(store.home):
        objective = store.get(identifier)
        delivery = objective.get('merge_delivery', {})
        if delivery.get('status') not in ('waiting', 'merging', 'cleanup'):
            return
        if delivery.get('next_check', 0) > time.time():
            return
        publication = objective['publication']
        payload = publication['payload']
        repo, number = payload['repository'], publication['pr']['number']
        try:
            if (publication['status'] != 'published' or delivery['commit'] != payload['commit']
                    or payload['branch'] != f"capo/{identifier}-{payload['tree'][:12]}"
                    or payload['branch'] == payload['base_branch']):
                raise ValueError('The approved change no longer matches this delivery')
            remote = gateway.status(repo, number)
            publication['remote_status'] = dict(remote, matches_verified_commit=remote['headRefOid'] == delivery['commit'])
            if remote['headRefOid'] != delivery['commit']:
                raise ValueError('The PR changed after approval')
            if remote['state'] == 'CLOSED':
                raise ValueError('The PR was closed without merging')
            if remote['state'] != 'MERGED':
                checks = remote.get('statusCheckRollup') or []
                states = [str(c.get('conclusion') or c.get('state') or c.get('status', '')).upper() for c in checks]
                if any(s in ('FAILURE', 'ERROR', 'CANCELLED', 'TIMED_OUT', 'ACTION_REQUIRED', 'STARTUP_FAILURE') for s in states):
                    raise ValueError('GitHub checks failed')
                if remote.get('reviewDecision') == 'CHANGES_REQUESTED':
                    raise ValueError('A reviewer requested changes')
                if remote.get('mergeStateStatus') == 'DIRTY':
                    raise ValueError('The PR has a merge conflict')
                delivery['next_check'] = time.time() + 60
                store.save(objective, 'merge_check_started')
                if not states or 'SUCCESS' not in states or any(s not in ('SUCCESS', 'NEUTRAL', 'SKIPPED') for s in states):
                    return
                if remote.get('isDraft'):
                    gateway.gh('pr', 'ready', str(number), '--repo', repo)
                    return
                if remote.get('mergeStateStatus') != 'CLEAN':
                    return
                delivery['status'] = 'merging'
                store.save(objective, 'merge_started')
                gateway.gh('pr', 'merge', str(number), '--repo', repo, '--squash',
                           '--match-head-commit', delivery['commit'])
                remote = gateway.status(repo, number)
                publication['remote_status'] = dict(remote, matches_verified_commit=remote['headRefOid'] == delivery['commit'])
                if remote['state'] != 'MERGED':
                    return  # A repository merge queue can finish later.
            delivery['status'] = 'cleanup'
            store.save(objective, 'merge_confirmed')
            head = gateway.remote_ref(objective['workspace'], repo, payload['branch'])
            if head is not None:
                if head != delivery['commit']:
                    raise ValueError('The old branch contains new work; it was not deleted')
                ref = 'refs/heads/' + payload['branch']
                git(objective['workspace'], '-c', 'credential.helper=', '-c',
                    'credential.helper=!gh auth git-credential', 'push',
                    f'--force-with-lease={ref}:{head}', f'https://github.com/{repo}.git',
                    ':' + ref, env=gateway.env)
            delivery['status'] = 'completed'
            store.save(objective, 'merge_delivery_completed')
        except (ValueError, RuntimeError, OSError, KeyError, TypeError):
            delivery['status'] = 'blocked'
            store.save(objective, 'merge_delivery_blocked')
