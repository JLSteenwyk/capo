"""Private, bounded adapter checkpoints for recurring read-only inspections."""
import json


def continuation(tools):
    snapshots = tools.snapshot()
    result = {}
    seen = set()
    for name, adapter in tools.adapters().items():
        if name not in snapshots or id(adapter) in seen:
            continue
        seen.add(id(adapter))
        value = snapshots[name]
        if callable(getattr(type(adapter), 'next_scan_state', None)):
            value = adapter.next_scan_state()
        if value:
            result[name] = value
    # Never grow recurring prompts without a bound or silently truncate a cursor.
    if len(json.dumps(result)) > 32000:
        return {'state': {}, 'gap': 'Saved scan position exceeds the continuation limit; narrow the next search window. Prior evidence remains in private run artifacts.'}
    return {'state': result, 'gap': ''}


def incomplete_report(receipts):
    successes = [r for r in receipts if isinstance(r.get('result'), dict)
                 and not r.get('error') and not r.get('uncertain')]
    messages = sum(len(r['result'].get('messages', [])) for r in successes if r.get('tool') == 'mail.read')
    coverage = f'{len(successes)} tool results preserved'
    if messages:
        coverage += f', including {messages} email excerpts'
    return {'findings': [],
            'blockers': [coverage + '. The scan is incomplete; no verified monitoring report was produced.'],
            'coverage': coverage + '; this is not a clean check. Resume saved unfinished work.'}
