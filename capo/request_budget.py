"""Pinned wall-clock budgets include queueing and provider recovery delays."""
import fcntl
import json
import os
import time
from .conversation import _write


def settings(value=None):
    value={} if value is None else value
    if not isinstance(value,dict) or set(value)-{'interactive_seconds','total_seconds'}:raise ValueError('Invalid request budget')
    result={'interactive_seconds':120,'total_seconds':900,**value}
    for k,low,high in (('interactive_seconds',5,120),('total_seconds',30,3600)):
        if type(result[k]) is not int or not low<=result[k]<=high:raise ValueError('Invalid '+k)
    if result['total_seconds']<result['interactive_seconds']:raise ValueError('Total budget must cover interactive window')
    return result


def pinned(directory,configured=None,received_at=None,now=None):
    now=time.time() if now is None else now
    directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd=os.open(directory/'budget.lock',os.O_CREAT|os.O_RDWR,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX)
        path=directory/'budget.json'
        if path.exists():return json.loads(path.read_text())
        config=settings(configured)
        start=now if received_at is None else min(now,received_at)
        result={**config,'received_at':start,'background_at':start+config['interactive_seconds'],'deadline':start+config['total_seconds']}
        _write(path,result);return result
    finally:os.close(fd)
