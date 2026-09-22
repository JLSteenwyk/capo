"""Read-only Gmail primitives and a composable mail research conversation."""
import base64
import html
import re
import json
import os
from pathlib import Path
from urllib.parse import quote
from .conversation import ConversationRouter, _write
from .contracts import TEXT, TEXTS, object_schema
from .research_tools import ReadTool, ReadTools, research
from .providers import Providers

TOKEN = Path.home()/'.config/capo/google-gmail-token.json'
SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']
DRAFT_SCOPE = 'https://www.googleapis.com/auth/gmail.compose'


def authorize(client_secrets, drafts=False):
    from google_auth_oauthlib.flow import InstalledAppFlow
    TOKEN.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    scopes = SCOPES + ([DRAFT_SCOPE] if drafts else [])
    flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets), scopes)
    credentials = flow.run_local_server(host='localhost', port=0, timeout_seconds=300,
        authorization_prompt_message='Opening Gmail sign-in in your browser.')
    if not credentials.refresh_token: raise RuntimeError('Offline Gmail access was not granted')
    _write(TOKEN, json.loads(credentials.to_json()))


class Gmail:
    def __init__(self, drafts=False):
        from google.auth.transport.requests import AuthorizedSession, Request
        from google.oauth2.credentials import Credentials
        if not TOKEN.exists(): raise RuntimeError('Gmail needs to be connected first.')
        TOKEN.chmod(0o600)
        stored = json.loads(TOKEN.read_text())
        scopes = stored.get('scopes', SCOPES)
        if isinstance(scopes, str): scopes = scopes.split()
        if drafts and DRAFT_SCOPE not in scopes:
            raise RuntimeError('Gmail draft permission requires reconnecting with --drafts.')
        self.drafts_enabled = drafts
        credentials = Credentials.from_authorized_user_file(str(TOKEN), scopes)
        if not credentials.valid:
            credentials.refresh(Request())
            _write(TOKEN, json.loads(credentials.to_json()))
        self.session = AuthorizedSession(credentials)

    def ready(self):
        return self

    def draft_write(self, method, draft_id='', payload=None):
        """Only draft CRUD, even though Google's compose scope also permits send."""
        if not self.drafts_enabled:
            raise ValueError('Draft permission is not enabled')
        if method not in ('POST', 'PUT', 'DELETE'):
            raise ValueError('Unsupported draft operation')
        if (method == 'POST' and draft_id) or (method != 'POST' and not re.fullmatch(r'[A-Za-z0-9_-]+', draft_id)):
            raise ValueError('Invalid draft ID')
        path = 'drafts' + ('/'+draft_id if draft_id else '')
        response = self.session.request(method, 'https://gmail.googleapis.com/gmail/v1/users/me/'+path,
                                        json=payload, timeout=20)
        from .service_errors import check
        check(response, 'Gmail')
        if not response.ok:
            raise RuntimeError('Gmail draft change was not confirmed')
        return {'deleted': True, 'id': draft_id} if method == 'DELETE' else response.json()

    def draft_exists(self, draft_id):
        if not re.fullmatch(r'[A-Za-z0-9_-]+',draft_id):raise ValueError('Invalid draft ID')
        response=self.session.get('https://gmail.googleapis.com/gmail/v1/users/me/drafts/'+draft_id,
                                  params={'format':'minimal'},timeout=20)
        if response.status_code==404:return False
        from .service_errors import check
        check(response, 'Gmail')
        if not response.ok:raise RuntimeError('Draft status could not be checked')
        return True

    def get(self, path, params):
        response = self.session.get('https://gmail.googleapis.com/gmail/v1/users/me/'+path,
                                   params=params, timeout=20)
        from .service_errors import check
        check(response, 'Gmail')
        if not response.ok: raise RuntimeError('Gmail access failed. Check the Google connection and Gmail API setup.')
        return response.json()

    def inbox(self):
        page = self.get('messages', {'labelIds':'INBOX', 'maxResults':20})
        items = []
        for row in page.get('messages', [])[:20]:
            message = self.get('messages/'+quote(row['id'],safe=''),
                               {'format':'metadata', 'metadataHeaders':['From','Subject','Date']})
            headers = {h['name'].lower():h['value'][:300] for h in message.get('payload',{}).get('headers',[])}
            items.append({'id':message.get('id',row['id']), 'from':headers.get('from',''), 'subject':headers.get('subject',''),
                          'date':headers.get('date',''), 'snippet':message.get('snippet','')[:700],
                          'unread':'UNREAD' in message.get('labelIds',[])})
        return {'messages':items, 'more_available':bool(page.get('nextPageToken')),
                'coverage':'Up to 20 inbox messages; headers and snippets only, not full messages or attachments.'}


    def messages(self, query, limit=25):
        if type(limit) is not int or not 1 <= limit <= 50 or len(query)>500:
            raise ValueError('Invalid email search')
        page=self.get('messages', {'q':query, 'maxResults':limit})
        rows=[]
        for item in page.get('messages',[])[:limit]:
            message=self.get('messages/'+quote(item['id'],safe=''), {'format':'full'})
            headers={h['name'].lower():h['value'][:300] for h in message.get('payload',{}).get('headers',[])}
            body=body_text(message.get('payload',{}))
            # Exclude quoted correspondence from the owner's style sample.
            body=re.split(r'(?m)^On .{0,300}wrote:\s*$|^[- ]*Original Message[- ]*$',body)[0]
            body='\n'.join(line for line in body.splitlines() if not line.lstrip().startswith('>'))
            rows.append({'id':message.get('id',item['id']), 'subject':headers.get('subject',''),
                         'from':headers.get('from',''), 'date':headers.get('date',''), 'body':body[:6000]})
        return {'messages':rows, 'more_available':bool(page.get('nextPageToken')),
                'coverage':'Matched messages; body excerpts up to 6000 characters each; no attachments. Quoted replies removed where recognizable.'}


def body_text(part, strip_quotes=True):
    if part.get('filename'):return ''
    children=part.get('parts',[])
    if children:
        texts=[body_text(p, strip_quotes=strip_quotes) for p in children]
        if part.get('mimeType')=='multipart/alternative':
            plain=next((text for p,text in zip(children,texts) if p.get('mimeType')=='text/plain' and text),'')
            rich=next((text for p,text in zip(children,texts) if p.get('mimeType')=='text/html' and text),'')
            # Some senders put only a subject stub in the plain alternative.
            # Keep the richer readable body in that case, within the same limits.
            if plain and len(plain.strip())<200 and len(rich.strip())>max(500,3*len(plain.strip())):
                return rich
            return plain or next((text for text in texts if text),'')
        return '\n'.join(texts)[:12000]
    data=part.get('body',{}).get('data','')
    if not data or part.get('mimeType') not in ('text/plain','text/html'):return ''
    text=base64.urlsafe_b64decode(data+'='*(-len(data)%4)).decode('utf-8',errors='replace')
    if part.get('mimeType')=='text/html':
        text=re.sub(r'<(script|style)\b[^>]*>.*?</\1>', '', text, flags=re.S|re.I)
        if strip_quotes:
            text=re.sub(r'<blockquote\b[^>]*>.*?</blockquote>', '', text, flags=re.S|re.I)
        text=html.unescape(re.sub('<[^>]+>',' ',text))
    return text[:12000]


class GmailReadTools(ReadTools):
    """Search and read are reusable across email tasks; no mailbox mutations."""

    def __init__(self, client):
        self.client = client
        self.known_ids = set()
        self.cursors = {}
        self.known_threads = set()
        self.read_attempts = 0
        self.characters = 0
        self.metadata_reads = 0
        self.inspected_ids = set()
        self.search_pages = []
        from .mail_attachments import MailAttachments
        self.attachments = MailAttachments(client, self.known_ids)
        super().__init__(self.attachments.tools() + [
            ReadTool('mail.search',
                'Search Gmail using query syntax such as in:sent, in:inbox, from:, subject:, '
                'after: and before:. Returns message IDs, not message text. Use an empty '
                'page_token initially; reuse returned next_page_token with the SAME query. '
                'page_size is a string integer 1–50.',
                object_schema({'query': TEXT, 'page_size': {'type': 'string',
                    'enum': [str(n) for n in range(1, 51)]}, 'page_token': TEXT}), self.search,fresh_for=300),
            ReadTool('mail.metadata','Triage up to 50 IDs returned by mail.search using sender, subject, date and short snippets before selecting full body excerpts. No bodies or attachments; not proof an entire message was read. At most 100 metadata reads per request.', object_schema({'ids':TEXTS}),self.metadata),
            ReadTool('mail.read',
                'Read up to 25 IDs returned by mail.search. Returns headers, labels and bounded '
                'body text, not attachments. At most 50 message reads and 120000 body characters '
                'per request. strip_quotes removes recognizable quoted replies; choose false '
                'when correspondence context matters. Retains truncation and source metadata.',
                object_schema({'ids': TEXTS, 'strip_quotes': {'type': 'boolean'}}), self.read),
            ReadTool('mail.thread', 'Read a thread ID returned by mail.search, including correspondence context. Bounded by the shared 50-message budget.',
                     object_schema({'id': TEXT}), self.thread,fresh_for=300),
        ])

    def next_scan_state(self):
        pending = self.known_ids - self.inspected_ids
        latest = {page['query']: page for page in self.search_pages}
        pages = [dict(page, ids=[i for i in page['ids'] if i in pending])
                 for page in latest.values() if page['next_page_token'] or any(i in pending for i in page['ids'])]
        if not pending and not pages:
            return {}
        return {'known_ids': sorted(pending), 'known_threads': [],
                'cursors': {p['next_page_token']: p['query'] for p in pages if p['next_page_token']},
                'search_pages': pages, 'inspected_ids': [], 'read_attempts': 0, 'characters': 0}

    def research_limit(self, name):
        if name=='mail.metadata' and self.metadata_reads>=100:return 'Metadata inspection allowance exhausted; retain partial coverage.'
        if name in ('mail.read', 'mail.thread') and (self.read_attempts >= 50 or self.characters >= 120000):
            return 'Mail read allowance exhausted for this request. Report partial findings; resume unread IDs in a later scheduled check.'
        return ''

    def research_state(self):
        return {'known_ids': sorted(self.known_ids), 'known_threads': sorted(self.known_threads),
                'cursors': self.cursors, 'search_pages': self.search_pages,
                'inspected_ids': sorted(self.inspected_ids), 'read_attempts': self.read_attempts,
                'characters': self.characters, 'metadata_reads':self.metadata_reads}

    def restore_research_state(self, state, continuation=False):
        # Called only with host-owned private checkpoints, never model arguments.
        self.known_ids.update(state.get('known_ids', []))
        self.known_threads.update(state.get('known_threads', []))
        self.cursors.update(state.get('cursors', {}))
        self.search_pages = state.get('search_pages', [])
        self.inspected_ids.update(state.get('inspected_ids', []))
        if not continuation:
            self.metadata_reads=max(self.metadata_reads,state.get('metadata_reads',0))
            self.read_attempts = max(self.read_attempts, state.get('read_attempts', 0))
            self.characters = max(self.characters, state.get('characters', 0))

    def thread(self,id):
        if id not in self.known_threads:raise ValueError('Search the thread first')
        data=self.client.get('threads/'+quote(id,safe=''),{'format':'minimal'})
        ids=list(dict.fromkeys(m['id'] for m in data.get('messages',[])))
        self.known_ids.update(ids)
        rows=[]
        attempted=0
        while attempted<len(ids) and self.read_attempts<50 and self.characters<120000:
            page=self.read(ids[attempted:attempted+25],strip_quotes=False)
            rows.append(page)
            attempted+=len(page['messages'])+len(page['errors'])
            if page['errors'] or page['unread_ids']:
                break
        unread=ids[attempted:]
        return {'thread_id':id,'pages':rows,'more_available':bool(unread),
                'unread_ids':unread,'status':'partial' if unread or any(p['errors'] for p in rows) else 'complete',
                'coverage':'Thread message excerpts within the shared read budget; attachments are separate.'}

    def search(self, query, page_size, page_token):
        if not query.strip() or len(query) > 500 or len(page_token) > 2000:
            raise ValueError('Invalid search')
        if page_token and self.cursors.get(page_token) != query:
            raise ValueError('Cursor was not returned for this query')
        params = {'q': query, 'maxResults': int(page_size)}
        if page_token:
            params['pageToken'] = page_token
        page = self.client.get('messages', params)
        rows = [{'id': row['id'], 'thread_id': row.get('threadId', '')}
                for row in page.get('messages', [])[:int(page_size)]]
        self.known_ids.update(row['id'] for row in rows)
        self.known_threads.update(row['thread_id'] for row in rows if row['thread_id'])
        cursor = page.get('nextPageToken', '')
        if cursor:
            self.cursors[cursor] = query
        self.search_pages.append({'query': query, 'page_token': page_token, 'next_page_token': cursor, 'ids': [r['id'] for r in rows]})
        return {'messages': rows, 'next_page_token': cursor, 'more_available': bool(cursor),
                'coverage': 'One search page; identifiers only. Read bodies before analyzing prose.'}

    def metadata(self,ids):
        if not 1<=len(ids)<=50 or len(set(ids))!=len(ids) or not set(ids)<=self.known_ids:
            raise ValueError('Use up to 50 IDs from a search')
        rows=[]
        for id in ids:
            if self.metadata_reads>=100:break
            self.metadata_reads+=1
            message=self.client.get('messages/'+quote(id,safe=''),{'format':'metadata','metadataHeaders':['From','Subject','Date']})
            headers={h['name'].lower():h['value'][:300] for h in message.get('payload',{}).get('headers',[])}
            rows.append({'id':id,'thread_id':message.get('threadId',''),'headers':headers,
                         'snippet':message.get('snippet','')[:700],'labels':message.get('labelIds',[])})
        return {'messages':rows,'unread_ids':ids[len(rows):], 'remaining_metadata_reads':100-self.metadata_reads,
                'coverage':'Headers and snippets only. Bodies and attachments have not been inspected.'}

    def read(self, ids, strip_quotes):
        if (not 1 <= len(ids) <= 25 or len(set(ids)) != len(ids)
                or not set(ids) <= self.known_ids or self.read_attempts >= 50
                or self.characters >= 120000):
            raise ValueError('Invalid IDs or exhausted message budget')
        rows, errors = [], []
        for message_id in ids:
            if self.characters >= 120000 or self.read_attempts >= 50:
                break
            # Attempts consume the budget too, including repeats and failed reads.
            self.read_attempts += 1
            try:
                message = self.client.get('messages/'+quote(message_id, safe=''), {'format': 'full'})
                payload = message.get('payload', {})
                headers = {h['name'].lower(): h['value'][:300] for h in payload.get('headers', [])}
                text = body_text(payload, strip_quotes=strip_quotes)
                source_truncated = len(text) >= 12000
                if strip_quotes:
                    text = re.split(r'(?m)^On .{0,300}wrote:\s*$|^[- ]*Original Message[- ]*$', text)[0]
                    text = '\n'.join(line for line in text.splitlines() if not line.lstrip().startswith('>'))
                limit = min(6000, 120000-self.characters)
                excerpt = text[:limit]
                self.characters += len(excerpt)
                self.inspected_ids.add(message_id)
                rows.append({'id': message_id, 'thread_id': message.get('threadId', ''),
                    'from': headers.get('from', ''), 'to': headers.get('to', ''),
                    'subject': headers.get('subject', ''), 'date': headers.get('date', ''),
                    'labels': message.get('labelIds', []), 'body': excerpt,
                    'truncated': len(text) > limit or source_truncated,
                    'authorship': 'Sent-mail candidate; review sender, signatures and quoted text.'
                        if 'SENT' in message.get('labelIds', []) else 'Sender-authored correspondence; not established as owner writing.',
                    'quotes_filtered': strip_quotes})
            except Exception as exc:
                from .providers import ServiceAuthenticationError
                from .recovery import RateLimited
                category = ('authentication_required' if isinstance(exc, ServiceAuthenticationError)
                    else 'permission_denied' if isinstance(exc, PermissionError)
                    else 'rate_limited' if isinstance(exc, RateLimited)
                    else 'service_unavailable' if isinstance(exc, ConnectionError)
                    else 'read_failed')
                errors.append({'id': message_id, 'error': 'Message could not be read.', 'category': category})
                if category != 'read_failed':
                    break
        return {'messages': rows, 'errors': errors,
                'unread_ids': ids[len(rows)+len(errors):],
                'status': 'partial' if errors or len(rows)<len(ids) else 'complete',
                'remaining_message_reads': 50-self.read_attempts,
                'remaining_body_characters': 120000-self.characters,
                'coverage': 'Body excerpts, not attachments. Empty bodies may be unavailable. '
                    'Quote filtering is heuristic; signatures and quoted authors need review.'}


class InboxConversation(ConversationRouter):
    # Short introduction plus the complete bounded document.
    reply_limit = 16000
    recoverable = True

    # Keep the route name for compatibility with existing Slack configuration.
    def __init__(self, home): super().__init__(home/'gmail')

    def _run(self, directory, context, schema, fd):
        try:
            result = research(Providers(timeout=90), GmailReadTools(Gmail()), context, directory,
                instructions='For an owner writing-style guide, search in:sent (default 25 samples) '
                'and read the messages with strip_quotes=true. Verify SENT labels and analyze only '
                'the owner’s prose, not signatures, quoted replies or team writing. Describe supported '
                'patterns with confidence proportional to the actual usable sample. Avoid private '
                'facts, names, addresses and distinctive passages in a reusable guide. For other '
                'mail requests choose suitable queries and preserve quoted context when needed. '
                'Do not replace a specific user request with an unrelated inbox summary.')
            reply = result['reply']
            _write(directory/'research.json', result)
            if result['document'].strip():
                _write(directory/'document.json', {'title': result['document_title'],
                    'content': result['document'], 'source': 'Gmail research',
                    'message_ids': sorted({m['id'] for receipt in result['receipts']
                        for m in receipt.get('result', {}).get('messages', []) if 'body' in m})})
        except Exception as exc:
            from .recovery import RetryLater
            if isinstance(exc, RetryLater):
                _write(directory/'retry.json', {'retry_at': exc.retry_at})
                os.close(fd)
                return
            reply = 'I couldn’t complete the email research. The connection, tool, or analysis failed; '
            reply += 'I have not sent or changed any email.'
        try:_write(directory/'outcome.json',{'route':{'action':'reply','repository':'','objective_id':'','reply':reply}})
        finally:os.close(fd)
