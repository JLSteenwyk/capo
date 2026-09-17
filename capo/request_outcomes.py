"""Validate a model's outcome inventory against host tool receipts."""
import json

from .contracts import TEXT,TEXTS,object_schema,validate


class OutcomeError(ValueError):
    """A fixed, host-authored explanation safe to return for correction."""


ITEM=object_schema({'requirement':TEXT,'kind':{'type':'string','enum':['answer','action','handoff']},
    'status':{'type':'string','enum':['complete','partial','needs_input']},
    'evidence':TEXTS,'next_step':TEXT})
REPORT=object_schema({'outcomes':{'type':'array','items':ITEM}})


def successful_action(receipt, tools, kind='action'):
    """Receipt eligibility shared by validation and factual failure summaries."""
    if 'error' in receipt or receipt.get('uncertain'):
        return False
    tool = tools.tools.get(receipt.get('tool'))
    value = receipt.get('result')
    if tool is None or not (tool.mutates or tool.verifies) or not isinstance(value, dict):
        return False
    if value.get('truncated') or value.get('confirmed') is False or value.get('verified') is False:
        return False
    if kind == 'action' and (value.get('completed') is False or value.get('status') in ('queued','running','pending')):
        return False
    return any(value.get(key) is True for key in
               ('verified','saved','created','deleted','updated','changed','already_exists','queued','completed'))


def completion_failure_reply(receipts, tools):
    """Report known effects without claiming the entire request was completed."""
    confirmations = []
    for receipt in receipts:
        if not successful_action(receipt, tools):
            continue
        value = receipt['result']
        reply = value.get('reply')
        if isinstance(reply, str) and reply.strip():
            text = reply.strip()[:500]
        else:
            text = 'Confirmed change from '+receipt['tool']+'.'
        if text not in confirmations:
            confirmations.append(text)
    if confirmations:
        return ('\n'.join(confirmations[:3])+
                '\n\nThese changes are confirmed. I could not verify whether the whole request is finished.')
    return ('I could not verify the completion report. Saved results are preserved; '
            'no successful change could be confirmed from them.')


def assess(encoded,receipts,tools):
    if len(encoded)>12000:raise OutcomeError('Outcome report exceeds its limit')
    report=json.loads(encoded)
    if report=={}:
        return {'status':'unassessed','outcomes':[],
                'coverage':'Legacy finish without an outcome inventory; completion has not been assessed.'}
    validate(report,REPORT)
    if not 1<=len(report['outcomes'])<=20:raise OutcomeError('List between one and twenty requested outcomes')
    for item in report['outcomes']:
        if not item['requirement'].strip() or len(item['requirement'])>500 or len(item['next_step'])>500:
            raise OutcomeError('Invalid outcome description')
        if item['status']!='complete' and not item['next_step'].strip():
            raise OutcomeError('Unfinished outcomes need a next step or a question')
        if len(item['evidence'])>40:raise OutcomeError('Too many evidence references')
        selected=[]
        for index in item['evidence']:
            if not index.isdigit() or len(index)>3 or int(index)>=len(receipts):
                raise OutcomeError('Outcome references an unknown receipt')
            selected.append(receipts[int(index)])
        if item['status']!='complete':continue
        if any('error' in r or r.get('uncertain') or 'result' not in r for r in selected):
            raise OutcomeError('Failed or uncertain receipts cannot prove completion')
        if item['kind'] in ('action','handoff'):
            if not any(successful_action(r,tools,item['kind']) for r in selected):
                cited=', '.join(index+' ('+receipts[int(index)].get('tool','report feedback')+')'
                                for index in item['evidence']) or 'none'
                candidates=', '.join(str(index)+' ('+r['tool']+')' for index,r in enumerate(receipts)
                                     if successful_action(r,tools,item['kind'])) or 'none'
                raise OutcomeError('Completed actions need a successful matching host mutation receipt. '
                    'Cited: '+cited+'. Eligible receipt indexes: '+candidates+
                    '. Use an eligible receipt only if its result establishes this specific outcome; do not repeat the write.')
    unfinished=[item for item in report['outcomes'] if item['status']!='complete']
    return {**report,'status':'partial' if unfinished else 'reported_complete',
            'coverage':'Model-declared requirements and evidence links checked against host receipts; semantic coverage and relevance still require evaluation.'}


def pending_text(report):
    return '\n'.join('- '+item['requirement']+': '+item['next_step']
                     for item in report['outcomes'] if item['status']!='complete')
