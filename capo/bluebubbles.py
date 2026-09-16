"""Bounded, fixed-endpoint BlueBubbles transport; no private iMessage APIs."""
import json
from urllib.parse import urlsplit, urlencode, quote
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler


class BridgeError(RuntimeError):pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise BridgeError('Bridge redirects are not allowed')


class BlueBubbles:
    def __init__(self, url, password):
        parsed = urlsplit(url)
        if (parsed.scheme not in ('http','https') or parsed.username or parsed.password or
                parsed.query or parsed.fragment or parsed.path not in ('','/') or not parsed.hostname):
            raise ValueError('Configure a bridge origin without credentials, path or query')
        if parsed.scheme=='http' and parsed.hostname not in ('127.0.0.1','localhost','::1'):
            raise ValueError('Remote bridge connections require HTTPS')
        if not password:raise ValueError('The private BlueBubbles password file is empty')
        self.url, self.password = url.rstrip('/'), password
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def request(self, path, payload=None, params=None, binary=False):
        query = urlencode(dict(params or {},password=self.password))
        request = Request(self.url+'/api/v1/'+path+'?'+query,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={'Content-Type':'application/json'})
        try:
            with self.opener.open(request,timeout=30) as response:
                raw = response.read(10_000_001)
                if len(raw)>10_000_000:raise BridgeError('Bridge response exceeds its size limit')
            if binary:return raw
            value=json.loads(raw)
            if value.get('status')!=200:raise BridgeError('Bridge request was not confirmed')
            return value.get('data')
        except Exception:
            # Do not expose the password-bearing URL or third-party error body.
            raise BridgeError('BlueBubbles request failed; inspect the private connection setup') from None

    def chat(self, guid):return self.request('chat/'+quote(guid,safe=''),params={'with':'participants'})

    def messages(self, guid, after, offset=0):
        return self.request('message/query',{'chatGuid':guid,'after':after,'offset':offset,
            'limit':100,'sort':'ASC','with':['chats','handle','attachments']})

    def send(self, guid, text, id):
        return self.request('message/text',{'chatGuid':guid,'message':text,'tempGuid':id,'method':'apple-script'})

    def attachment(self, guid):
        return self.request('attachment/'+quote(guid,safe='')+'/download',params={'force':'false','original':'true'},binary=True)
