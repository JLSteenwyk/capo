"""Host-owned report snapshot policy; freshness never authorizes replaying effects."""
import math


def snapshot(observed_at, *, rebuildable=False, max_age=300):
    return {'observed_at':observed_at,'max_age':max_age,'rebuildable':rebuildable}


def from_receipts(receipts, tools, now):
    observations=[]
    for receipt in receipts:
        tool=tools.tools.get(receipt.get('tool'))
        ttl=getattr(tool,'fresh_for',0)
        if ttl and not tool.mutates and not tool.verifies and 'result' in receipt:
            observations.append(snapshot(receipt.get('observed_at'),max_age=ttl))
    # Reports with no volatile reads retain a bounded delivery window too.
    return {'observations':observations or [snapshot(now,max_age=900)],'rebuildable':False}


def stale(value, now):
    observed=value.get('observed_at');ttl=value.get('max_age')
    return not (type(observed) in (int,float) and math.isfinite(observed)
        and type(ttl) in (int,float) and 0<ttl<=900 and 0<=now-observed<=ttl)


def guard(run, now):
    """Only unsent prepared reports may be invalidated. Sending is reconciliation-only."""
    if run.get('status')!='ready' or 'evidence_snapshot' not in run:return True
    value=run['evidence_snapshot']
    observations=value.get('observations',[value])
    if observations and all(not stale(item,now) for item in observations):return True
    run['error_summary']='Report evidence expired before delivery. Current sources must be checked again.'
    if value.get('rebuildable') is True and run.get('attempts',0)<3:
        run.update(status='queued',retry_at=0)
        for key in ('payload','wire_text','wire_mrkdwn','evidence_snapshot'):run.pop(key,None)
    else:
        run.update(status='failed',outcome_status='partial')
    return False
