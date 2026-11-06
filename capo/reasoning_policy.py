"""One reasoning preference and optional outage fallback, separate from roles."""
import json

PROVIDERS=('codex','claude','grok')


def settings(config=None):
    value=(config or {}).get('reasoning',{})
    if not isinstance(value,dict) or set(value)-{'primary','fallback'}:raise ValueError('Invalid reasoning policy')
    primary=value.get('primary','codex');fallback=value.get('fallback',None)
    if primary not in PROVIDERS or fallback not in (*PROVIDERS,None) or fallback==primary:raise ValueError('Choose a primary provider and a distinct optional fallback')
    return {'primary':primary,'fallback':fallback}


def selected(config=None,directory=None,explicit=None):
    if explicit is not None:
        if explicit not in PROVIDERS:raise ValueError('Unknown explicit provider')
        return explicit
    if directory is not None:
        checkpoint=directory/'checkpoint.json'
        if checkpoint.exists():
            saved=json.loads(checkpoint.read_text()).get('reasoning_provider','claude')
            if saved is not None:
                if saved not in PROVIDERS:raise ValueError('Unknown pinned reasoning provider')
                return saved
    return settings(config)['primary']


def recovery(config=None):
    chosen=settings(config)
    value=dict((config or {}).get('recovery',{}))
    # A pinned older recovery policy remains the caller's responsibility on resume.
    value['prefer_primary']=True
    value['fallbacks']={chosen['primary']:[chosen['fallback']]} if chosen['fallback'] else {}
    return value


def request_recovery(config,directory):
    # Recovery policies participate in prompt identity. Preserve existing journals
    # on resume; only new requests adopt the consolidated policy.
    for path in sorted(directory.glob('step-*/recovery.json')):
        value=json.loads(path.read_text())
        if 'policy' in value:return value['policy']
    return recovery(config)
