"""Owner-only Slack routing for the separate browser worker."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from . import browser
from .conversation import _write


def sessions(service):
    root = service.store.home / 'browser' / 'sessions'
    for path in root.glob('*/state.json'):
        state = json.loads(path.read_text())
        identity = state.get('slack') or {}
        if all(identity.get(k) == service.config[k] for k in ('team_id', 'channel_id', 'owner_user_id')):
            yield state


def start(service, event_id, event, request):
    settings = service.config.get('browser', {})
    if not settings.get('enabled'):
        return 'Browser tasks are not enabled on this computer yet.'
    urls = re.findall(r'https://[^\s<>|]+', request)
    url = urls[0] if urls else settings.get('start_url')
    if not url:
        return 'Which website or theater should I use?'
    identity = {k: service.config[k] for k in ('team_id', 'channel_id', 'owner_user_id')}
    identity.update(ts=event['ts'], thread_ts=event.get('thread_ts', event['ts']))
    state = browser.create(service.store.home, request, url, settings.get('allowed_origins', []),
                           source='slack:' + event_id, slack=identity)
    directory = browser.location(service.store.home, state['id'])
    if state['status'] == 'queued':
        state['preferences'] = settings.get('preferences', {})
        _write(directory / 'state.json', state)
    return 'I’ll open my browser and help with that. I’ll ask before clicking, entering information, or paying.'


def dispatch(service, event, text):
    thread = event.get('thread_ts', event['ts'])
    matching = sorted((s for s in sessions(service) if s['slack']['thread_ts'] == thread),
                      key=lambda s: s['created'], reverse=True)
    if not matching:
        return None
    state = matching[0]
    directory = browser.location(service.store.home, state['id'])
    command = text.strip().casefold()
    if command in ('cancel', 'stop', 'cancel this request', 'stop browser'):
        browser.cancel(service.store.home, state['id'])
        return 'I’m stopping the browser task.'
    if command in ('status', 'browser status'):
        return state['message']
    if command == 'approve' or command == 'approve browser':
        if state['status'] != 'awaiting_approval':
            return 'There is no browser step waiting for approval.'
        review = directory / 'review.json'
        if not review.exists() or json.loads(review.read_text()).get('digest') != state['pending']['digest']:
            return 'Please wait for the browser step’s review message first.'
        if float(event['ts']) < state['pending'].get('created', 0):
            return 'That approval belongs to an earlier browser step. Please review the latest one.'
        browser.approve(service.store.home, state['id'], state['pending']['digest'])
        return 'Approved. I’ll carry out that one step.'
    if state['status'] == 'needs_input':
        if len(text) > 8000:
            return 'Please send a shorter reply.'
        _write(directory / 'answer.json', {'text': text})
        return 'Thanks—I’ll use that.'
    return None


def tick(service):
    if not service.config.get('browser', {}).get('enabled'):
        return
    if not hasattr(service, 'browser_processes'):
        service.browser_processes = {}
    states = sorted(sessions(service), key=lambda s: s['created'])
    busy = any(s['status'] in ('starting', 'running', 'awaiting_approval', 'needs_input', 'executing') for s in states)
    for state in states:
        directory = browser.location(service.store.home, state['id'])
        process = service.browser_processes.get(state['id'])
        if state['status'] == 'queued' and (directory / 'cancel.json').exists():
            state.update(status='cancelled', message='I stopped the browser task.')
            _write(directory / 'state.json', state)
        if process is None and state['status'] in ('starting', 'running', 'awaiting_approval', 'needs_input', 'executing'):
            launch = directory / 'launch.json'
            pid = state.get('pid') or (json.loads(launch.read_text()).get('pid') if launch.exists() else None)
            alive = False
            if pid:
                try:
                    os.kill(pid, 0); alive = True
                except ProcessLookupError:
                    pass
                except PermissionError:
                    alive = True
            if not alive:
                state.update(status='blocked', message='The browser was interrupted. Check the website before starting again.')
                _write(directory / 'state.json', state)
        if process is not None and process.poll() is not None and state['status'] not in ('completed', 'blocked', 'cancelled'):
            state.update(status='blocked', message='The browser stopped unexpectedly. Check the website before restarting.')
            _write(directory / 'state.json', state)
        if state['status'] == 'queued' and not busy:
            state['status'] = 'starting'
            _write(directory / 'state.json', state)
            env = os.environ.copy()
            for key in ('SLACK_APP_TOKEN', 'SLACK_BOT_TOKEN', 'GH_TOKEN', 'GITHUB_TOKEN',
                        'ANTHROPIC_API_KEY', 'OPENAI_API_KEY', 'XAI_API_KEY', 'CODEX_API_KEY'):
                env.pop(key, None)
            with (directory / 'runner.log').open('a') as log:
                service.browser_processes[state['id']] = subprocess.Popen(
                    [sys.executable, '-m', 'capo', '--home', str(service.store.home), 'browser-run', state['id']],
                    cwd=Path(__file__).resolve().parent.parent, env=env, stdout=log, stderr=log,
                    start_new_session=True)
            _write(directory / 'launch.json', {'pid': service.browser_processes[state['id']].pid})
            busy = True
        if state['status'] not in ('awaiting_approval', 'needs_input', 'completed', 'blocked', 'cancelled'):
            continue
        text = state['message']
        digest = ''
        if state['status'] == 'awaiting_approval':
            digest = state['pending']['digest']
            text += '\n' + state['pending']['effect'] + '\nReply approve to allow this step, or cancel to stop.'
        checkpoint = hashlib.sha256((state['status'] + text + digest).encode()).hexdigest()
        notice = directory / 'notice.json'
        if notice.exists() and json.loads(notice.read_text()).get('checkpoint') == checkpoint:
            continue
        if service.send_chunk(state['slack'], text):
            _write(notice, {'checkpoint': checkpoint})
            if digest:
                _write(directory / 'review.json', {'digest': digest})


def stop(service):
    for state in sessions(service):
        if state['status'] in ('starting', 'running', 'awaiting_approval', 'needs_input', 'executing'):
            browser.cancel(service.store.home, state['id'])
    for process in getattr(service, 'browser_processes', {}).values():
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
