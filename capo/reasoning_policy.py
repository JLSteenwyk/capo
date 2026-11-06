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


def direct_call(backend,primary,prompt,schema,cwd,directory,config=None,images=None):
    """One-shot synthesis/vision: one outage fallback inside the original time budget.

    Shared tool loops keep their durable RecoveringProvider instead. This adapter
    handles standalone reasoning only; it never invokes or retries a tool action.
    """
    import time
    from .conversation import _write
    from .recovery import failure
    chosen=settings(config);fallback=chosen['fallback'] if primary==chosen['primary'] else None
    if images and fallback not in ('codex','claude'):fallback=None
    kwargs={'images':images} if images else {}
    if not fallback:return backend.call(primary,prompt,schema,cwd,directory,**kwargs)
    directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    receipt={'primary':primary,'fallback':fallback,'attempts':[]}
    original=getattr(backend,'deadline',None);limit=getattr(backend,'timeout',None)
    bounded=type(limit) in (int,float) and limit>0
    if bounded:backend.deadline=min(original,time.time()+limit) if type(original) in (int,float) else time.time()+limit
    try:
        receipt['attempts'].append({'provider':primary})
        _write(directory/'fallback-receipt.json',receipt)
        try:
            result=backend.call(primary,prompt,schema,cwd,directory,**kwargs)
        except Exception as exc:
            category,_=failure(exc,time.time())
            receipt['attempts'][-1]['failure']=category
            _write(directory/'fallback-receipt.json',receipt)
            if category not in ('temporary','rate_limit','unavailable'):raise
            if bounded and time.time()>=backend.deadline:raise
            receipt['attempts'].append({'provider':fallback})
            _write(directory/'fallback-receipt.json',receipt)
            try:result=backend.call(fallback,prompt,schema,cwd,directory/('fallback-'+fallback),**kwargs)
            except Exception:
                receipt['fallback_failed']=True
                _write(directory/'fallback-receipt.json',receipt)
                raise
            receipt['selected']=fallback
        else:receipt['selected']=primary
        _write(directory/'fallback-receipt.json',receipt)
        return result
    finally:
        if bounded:backend.deadline=original
