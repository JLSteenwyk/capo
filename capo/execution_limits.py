"""Host-configured limits pinned for the lifetime of each shared request."""
import hashlib
import json


def settings(value=None):
    if value is None:value={}
    if not isinstance(value,dict) or set(value)-{'max_calls','evidence_chars'}:
        raise ValueError('execution accepts max_calls and evidence_chars only')
    result={'max_calls':10,'evidence_chars':180000,**value}
    for key,low,high in (('max_calls',1,40),('evidence_chars',1000,360000)):
        if type(result[key]) is not int or not low<=result[key]<=high:
            raise ValueError('Invalid execution.'+key)
    return result


def pinned(state,request,configured):
    proposed=settings(configured)
    if 'execution_limits' in state:return settings(state['execution_limits'])
    if state.get('request_identity'):
        # Version-two checkpoints encoded the original tool limit in identity.
        matches=[n for n in range(1,41) if hashlib.sha256(
            json.dumps([request,n],sort_keys=True).encode()).hexdigest()==state['request_identity']]
        if len(matches)!=1:raise ValueError('Cannot verify original execution budget')
        proposed={'max_calls':matches[0],'evidence_chars':180000}
    elif state.get('fingerprint'):
        # Older ordinary CapabilityConversation requests always used ten calls.
        # The existing checkpoint binder still authenticates the original input.
        proposed={'max_calls':10,'evidence_chars':180000}
    state['execution_limits']=proposed
    return proposed
