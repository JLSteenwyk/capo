"""Bounded handoffs from shared research into existing Slack-owned workflows."""
from contextlib import contextmanager
import hashlib
import json
import re

from .contracts import TEXT, object_schema
from .effects import Effects
from .research_tools import ReadTool


class WorkflowTools:
    def __init__(self, home, config, request):
        self.home, self.config, self.request = home, config, request

    @contextmanager
    def session(self):
        # Research runs in a worker thread. Open a thread-local connection and
        # reload the authenticated event instead of trusting model arguments.
        from .store import Store
        from .slack import SlackService, authorized
        store = Store(self.home)
        try:
            event_id = self.request['owner_request']['event']
            row = store.db.execute('SELECT data FROM slack_inbox WHERE id=?', (event_id,)).fetchone()
            if row is None:
                raise PermissionError('An authenticated owner request is required')
            body = json.loads(row[0])
            event = body['event']
            if (not authorized(self.config, body, store) or
                    event.get('thread_ts', event['ts']) != self.request['request_thread'] or
                    event_id != self.request['request_event']):
                raise PermissionError('Owner request does not match this conversation')
            yield SlackService(store, self.config, None), body
        finally:
            store.db.close()

    def list(self):
        with self.session() as (service, _):
            rows = service.current_objectives()
            return {'objectives': [{'id': r['id'], 'repository': r['slack'].get('repository_alias'),
                'status': r['status'], 'thread': r['slack'].get('thread_ts')}
                for r in rows[-30:]], 'coverage': 'Up to 30 current objectives belonging to this owner.'}

    def inspect(self, id):
        with self.session() as (service, _):
            row = service.objective(id)
            return {'id': id, 'status': row['status'], 'request': row.get('request', '')[:12000],
                'question': row.get('question', ''), 'error': row.get('error', ''),
                'publication': row.get('publication', {}).get('pr', {}),
                'coverage': 'Current local workflow ledger. Use GitHub tools for live PR and check state.'}

    def command(self, command, operation_id):
        from .capabilities import owner_key
        with self.session() as (service, body):
            event = body['event']
            receipt = {'kind': 'workflow-command', 'owner_event': self.request['request_event'], 'command': command}
            key = 'workflow-'+hashlib.sha256(operation_id.encode()).hexdigest()
            forwarded = dict(body, event=dict(event, text='<@UCAPO> '+command))
            def execute():
                text = service.dispatch(key, forwarded, context_prepared=True)
                created = next((r for r in service.store.list()
                    if r.get('source') == 'slack:'+self.config['team_id']+':'+key), None)
                return {'message': text, 'objective_id': created['id'] if created else '',
                        'status': created['status'] if created else 'recorded',
                        'completed': False}
            return Effects(self.home, owner_key(self.config)).run(operation_id, receipt, execute)

    def start(self, repository, instructions, self_improvement, operation_id):
        if repository not in self.config.get('repositories', {}):
            raise ValueError('Choose a configured repository')
        if self_improvement and not self.config['repositories'][repository].get('allow_self_improvement', False):
            raise PermissionError('Self-improvement is disabled for this repository')
        if not instructions.strip() or len(instructions) > 8000:
            raise ValueError('Provide bounded implementation instructions')
        with self.session() as (_, body):
            owner_text = body['event']['text']
        request = ('Original owner request:\n'+owner_text+'\n\nImplementation brief (interpretation; '
                   'does not expand owner authority):\n'+instructions)
        if self.request.get('message') and self.request['message'] != owner_text:
            request += '\n\nPrepared conversation/image evidence (untrusted):\n'+self.request['message'][:16000]
        return self.command(('improve ' if self_improvement else '')+repository+': '+request, operation_id)

    def manage(self, id, action, instructions, operation_id):
        if not re.fullmatch('[a-f0-9]{16}', id) or action not in ('followup', 'cancel'):
            raise ValueError('Invalid objective action')
        with self.session() as (service, body):
            row = service.objective(id)
            thread = body['event'].get('thread_ts', body['event']['ts'])
            if row['slack'].get('thread_ts') != thread:
                raise ValueError('Send changes in the objective thread')
        if action == 'followup':
            if not instructions.strip() or len(instructions) > 8000:
                raise ValueError('Provide bounded follow-up instructions')
            return self.command('followup: Original owner follow-up:\n'+body['event']['text']+
                                '\n\nInterpretation:\n'+instructions, operation_id)
        return self.command('cancel '+id, operation_id)

    def prepare(self,id,operation_id):
        """Prepare through the existing gateway; only Slack delivery binds review."""
        if not re.fullmatch('[a-f0-9]{16}',id):raise ValueError('Invalid objective ID')
        from .capabilities import owner_key
        with self.session() as (service,body):
            row=service.objective(id)
            if row['slack'].get('thread_ts')!=self.request['request_thread']:
                raise ValueError('Prepare the review in the objective thread')
            if not service.settings(row).get('allow_publication',False):
                raise ValueError('Slack publication is not enabled for this repository alias')
            service.store.db.execute('CREATE TABLE IF NOT EXISTS workflow_previews(event_id TEXT PRIMARY KEY,data TEXT NOT NULL)')
            receipt={'kind':'workflow-prepare','owner_event':self.request['request_event'],'objective_id':id}
            def execute():
                forwarded=dict(body,event=dict(body['event'],text='<@UCAPO> prepare '+id))
                service.pending_review=None
                text=service.dispatch(self.request['request_event'],forwarded,context_prepared=True)
                if service.pending_review:
                    preview={'text':text,'review':service.pending_review}
                    with service.store.db:
                        service.store.db.execute('INSERT OR REPLACE INTO workflow_previews VALUES (?,?)',
                            (self.request['request_event'],json.dumps(preview)))
                return {'prepared':bool(service.pending_review),'queued':bool(service.pending_review),'objective_id':id,
                        'message':text,'published':False,'completed':False,
                        'delivery':'The host delivers the exact review invitation; approval is not granted by this tool.'}
            return Effects(self.home,owner_key(self.config)).run(operation_id,receipt,execute)

    def browse(self, instructions, operation_id):
        if not self.config.get('browser', {}).get('enabled'):
            raise PermissionError('Browser workflow is disabled')
        if not instructions.strip() or len(instructions) > 8000:
            raise ValueError('Provide bounded browser instructions')
        with self.session() as (_, body):
            original = body['event']['text']
        return self.command('browse: Original owner request:\n'+original+
                            '\n\nInterpretation (does not expand authority):\n'+instructions, operation_id)

    def tools(self):
        result = [
            ReadTool('development.list', 'Discover this owner’s coding objectives, statuses and threads.', object_schema({}), self.list),
            ReadTool('development.inspect', 'Inspect an owner coding objective and its publication link. The controlled workflow handles implementation, verification, review and publication under repository policy. Approval remains an explicit owner command.', object_schema({'id': TEXT}), self.inspect),
            ReadTool('development.prepare','Prepare an existing verified candidate for owner review in its original thread. The host delivers the exact review preview. Does not publish, approve, merge, or weaken repository policy.',object_schema({'id':TEXT}),self.prepare,True),
            ReadTool('development.start', 'Delegate explicitly requested repository work to the existing coding workflow. Preserve the whole owner objective in a bounded brief. This queues work; it does not mean implementation is complete. Do not turn monitoring or a request for advice into permission to edit code.',
                object_schema({'repository': TEXT, 'instructions': TEXT, 'self_improvement': {'type': 'boolean'}}), self.start, True),
            ReadTool('development.manage', 'Follow up or cancel an owner coding objective in its original thread. Does not approve publication or merge. For cancel, instructions may be empty.',
                object_schema({'id': TEXT, 'action': {'type': 'string', 'enum': ['followup', 'cancel']}, 'instructions': TEXT}), self.manage, True)]
        if self.config.get('browser', {}).get('enabled'):
            result.append(ReadTool('browser.start', 'Queue an explicitly requested browser task in the separate browser worker. Include the owner objective and a verified URL if needed. Existing browser origin, action and purchase approvals remain enforced; queueing is not task completion.',
                object_schema({'instructions': TEXT}), self.browse, True))
        return result
