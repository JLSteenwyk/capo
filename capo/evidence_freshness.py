"""Host timestamps for volatile evidence; immutable history is not made current."""
import math
import time


def freshness(receipt, tools, now=None):
    tool=tools.tools.get(receipt.get('tool'))
    ttl=getattr(tool,'fresh_for',0)
    if not ttl or tool.mutates or tool.verifies:
        return 'historical'
    observed=receipt.get('observed_at')
    if type(observed) not in (int,float) or not math.isfinite(observed):
        return 'unknown'
    age=(time.time() if now is None else now)-observed
    return 'fresh' if 0<=age<=ttl else 'stale'


def describe(receipts,tools):
    now=time.time()
    return [{**row,'receipt_index':str(index),'freshness':freshness(row,tools,now)}
            for index,row in enumerate(receipts)]
