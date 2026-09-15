"""Preserve request identity and in-flight provider inputs across capability updates."""
import hashlib
import json

from .conversation import _write
from .recovery import RecoveryStopped


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def legacy_prompt(directory, schema):
    """Recover only a provider input authenticated by its saved recovery identity."""
    path = directory/'recovery.json'
    if not path.exists():
        return None
    identity = json.loads(path.read_text())['identity']
    for candidate in sorted(directory.glob('*/prompt.txt'), reverse=True):
        if candidate.stat().st_size > 2_000_000:
            continue
        text = candidate.read_text()
        start = text.find('Current local time: ')
        if start < 0:
            continue
        prompt = text[start:]
        if digest(['claude', prompt, schema, None]) == identity:
            return prompt
    raise RecoveryStopped('Saved provider input could not be verified; receipts are preserved')


def bind(state, request, catalog, instructions, max_calls, directory, schema):
    """Allow capability changes, never an unrelated request or reset work budget."""
    identity = digest([request, max_calls])
    if 'request_identity' in state:
        if state['request_identity'] != identity:
            raise RecoveryStopped('Research input changed; start a new continuation')
    elif state['fingerprint'] != digest([request, catalog, instructions, max_calls]):
        verified = False
        for step in sorted(directory.glob('step-*')):
            try:
                prompt = legacy_prompt(step, schema)
                data = json.loads(prompt.splitlines()[-1]) if prompt else None
                candidates = [instructions]
                marker = 'Never claim an action succeeded without a successful mutation receipt. '
                if prompt and marker in prompt:
                    candidates.append(prompt.split(marker, 1)[1].rsplit('\n', 1)[0])
                if (data and data.get('request') == request and any(
                        state['fingerprint'] == digest([request, data['tools'], prior, max_calls])
                        for prior in candidates)):
                    verified = True
                    break
            except (ValueError, KeyError, RecoveryStopped):
                continue
        if not verified:
            raise RecoveryStopped('Original research input could not be verified; receipts are preserved')
    state['request_identity'] = identity
    state['version'] = 2
    state['capability_identity'] = digest([catalog, instructions])


def provider_prompt(directory, proposed, schema):
    """A deferred provider call must keep its exact input and recovery directory."""
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory/'input.json'
    if path.exists():
        saved = json.loads(path.read_text())
        if saved['identity'] != digest(['claude', saved['prompt'], schema, None]):
            raise RecoveryStopped('Saved research provider input changed')
        return saved['prompt']
    prompt = legacy_prompt(directory, schema) or proposed
    _write(path, {'prompt': prompt, 'identity': digest(['claude', prompt, schema, None])})
    return prompt
