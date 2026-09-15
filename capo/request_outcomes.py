"""Validate a model's outcome inventory against host tool receipts."""
import json

from .contracts import TEXT,TEXTS,object_schema,validate


ITEM=object_schema({'requirement':TEXT,'kind':{'type':'string','enum':['answer','action','handoff']},
    'status':{'type':'string','enum':['complete','partial','needs_input']},
    'evidence':TEXTS,'next_step':TEXT})
REPORT=object_schema({'outcomes':{'type':'array','items':ITEM}})


def assess(encoded,receipts,tools):
    if len(encoded)>12000:raise ValueError('Outcome report exceeds its limit')
    report=json.loads(encoded)
    if report=={}:
        return {'status':'unassessed','outcomes':[],
                'coverage':'Legacy finish without an outcome inventory; completion has not been assessed.'}
    validate(report,REPORT)
    if not 1<=len(report['outcomes'])<=20:raise ValueError('List between one and twenty requested outcomes')
    for item in report['outcomes']:
        if not item['requirement'].strip() or len(item['requirement'])>500 or len(item['next_step'])>500:
            raise ValueError('Invalid outcome description')
        if item['status']!='complete' and not item['next_step'].strip():
            raise ValueError('Unfinished outcomes need a next step or a question')
        if len(item['evidence'])>40:raise ValueError('Too many evidence references')
        selected=[]
        for index in item['evidence']:
            if not index.isdigit() or len(index)>3 or int(index)>=len(receipts):
                raise ValueError('Outcome references an unknown receipt')
            selected.append(receipts[int(index)])
        if item['status']!='complete':continue
        if any('error' in r or r.get('uncertain') or 'result' not in r for r in selected):
            raise ValueError('Failed or uncertain receipts cannot prove completion')
        if item['kind'] in ('action','handoff'):
            eligible=[]
            for receipt in selected:
                tool=tools.tools.get(receipt['tool']);value=receipt.get('result')
                if tool is None or not (tool.mutates or tool.verifies) or not isinstance(value,dict):continue
                if value.get('truncated') or value.get('confirmed') is False or value.get('verified') is False:continue
                if item['kind']=='action' and (value.get('completed') is False or value.get('status') in ('queued','running','pending')):continue
                if any(value.get(key) is True for key in ('verified','saved','created','deleted','updated','changed','already_exists','queued','completed')):
                    eligible.append(receipt)
            if not eligible:raise ValueError('Completed actions need a successful matching host mutation receipt')
    unfinished=[item for item in report['outcomes'] if item['status']!='complete']
    return {**report,'status':'partial' if unfinished else 'reported_complete',
            'coverage':'Model-declared requirements and evidence links checked against host receipts; semantic coverage and relevance still require evaluation.'}


def pending_text(report):
    return '\n'.join('- '+item['requirement']+': '+item['next_step']
                     for item in report['outcomes'] if item['status']!='complete')
